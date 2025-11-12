import logging
import asyncio
import aiohttp
import time
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from app.services.metadata_client import metadata_client, extract_internal_phone_from_channel, extract_line_id_from_exten
from app.utils.logger_client import call_logger
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    get_last_call_info,
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    bridge_store,
    bridge_store_by_chat,
    # Новые функции для группировки событий
    get_phone_for_grouping,
    should_send_as_comment,
    should_replace_previous_message,
    update_phone_tracker,
    is_internal_number,
)

async def process_dial(bot: Bot, chat_id: int, data: dict):
    """
    Модернизированный обработчик события 'dial' (17.01.2025):
    - Использует новую систему группировки по номеру телефона
    - Применяет форматы сообщений из файла "Пояснение"  
    - Правильно заменяет start сообщения или отправляет комментарии
    - Поддерживает сложные сценарии с несколькими Extensions
    """

    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

    # Получаем номер для группировки событий
    phone_for_grouping = get_phone_for_grouping(data)

    # ───────── Шаг 1. Извлечение данных ─────────
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int = call_type == 2
    callee = exts[0] if exts else ""
    token = data.get("Token", "")
    trunk_info = data.get("Trunk", "")
    
    # Получаем номер предприятия из БД по Token (name2)
    from app.services.postgres import get_pool
    enterprise_number = "0000"  # fallback
    try:
        pool = await get_pool()
        if pool:
            async with pool.acquire() as conn:
                ent_row = await conn.fetchrow(
                    "SELECT number FROM enterprises WHERE name2 = $1 LIMIT 1",
                    token
                )
                if ent_row:
                    enterprise_number = ent_row["number"]
                    logging.info(f"[process_dial] Resolved Token '{token}' -> enterprise '{enterprise_number}'")
                else:
                    logging.warning(f"[process_dial] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_dial] Failed to resolve enterprise_number: {e}")

    logging.info(f"[process_dial] RAW DATA = {data!r}")
    logging.info(f"[process_dial] Phone for grouping: {phone_for_grouping}, call_type: {call_type}")
    
    # ───────── Логирование dial события в Call Logger (ФОНОВО) ─────────
    try:
        await call_logger.log_call_event(
            enterprise_number=enterprise_number,
            unique_id=uid,
            event_type="dial",
            event_data=data,
            phone_number=phone,
            chat_id=chat_id,
            background=True  # Отправляем в фоне, не блокируем обработку
        )
        logging.info(f"[process_dial] Queued dial event to Call Logger: {uid}")
    except Exception as e:
        logging.warning(f"[process_dial] Failed to queue dial event: {e}")

    # ───────── Шаг 2. Проверяем, нужно ли заменить предыдущее сообщение ─────────
    should_replace, message_to_delete = should_replace_previous_message(phone_for_grouping, 'dial', chat_id)
    
    if should_replace and message_to_delete:
        try:
            await bot.delete_message(chat_id, message_to_delete)
            logging.info(f"[process_dial] Deleted previous message {message_to_delete}")
        except Exception as e:
            logging.warning(f"[process_dial] Failed to delete message {message_to_delete}: {e}")

    # Удаляем прошлый "start"-месседж из bridge_store (старая логика)
    if uid in bridge_store_by_chat[chat_id]:
        try:
            if not should_replace:  # Если уже удалили выше, не удаляем дважды
                await bot.delete_message(chat_id, bridge_store_by_chat[chat_id].pop(uid))
        except Exception:
            pass

    # ───────── Шаг 2.5. Получаем обогащённые метаданные ─────────
    # Извлекаем данные для обогащения
    line_id = extract_line_id_from_exten(trunk_info)  # ID линии из Trunk
    internal_phone = None
    external_phone = None
    
    # Определяем внутренний и внешний номера
    if is_int:
        # Внутренний звонок
        internal_phone = data.get("CallerIDNum", "") if is_internal_number(data.get("CallerIDNum", "")) else None
    else:
        # Внешний звонок
        external_phone = raw_phone
        
        # Ищем внутренний номер
        if exts:
            for ext in exts:
                if is_internal_number(ext):
                    internal_phone = ext
                    break
        
        if not internal_phone:
            caller_id = data.get("CallerIDNum", "")
            if is_internal_number(caller_id):
                internal_phone = caller_id
    
    # ───────── Используем pre-enriched данные (уже сделано в main.py) ─────────
    enriched_data = data.get("_enriched_data", {})
    
    if enriched_data:
        logging.info(f"[process_dial] Using pre-enriched data: {enriched_data}")
        
        # Логируем HTTP запрос (для этого chat_id, но запрос был сделан один раз)
        try:
            await call_logger.log_http_request(
                enterprise_number=enterprise_number,
                unique_id=uid,
                method="POST",
                url=f"http://localhost:8020/metadata/{enterprise_number}/enrich",
                request_data={
                    "line_id": line_id,
                    "internal_phone": internal_phone,
                    "external_phone": external_phone,
                    "short_names": False
                },
                response_data=enriched_data,
                status_code=200,
                duration_ms=0,  # Уже было сделано в main.py
                background=True
            )
        except Exception as log_e:
            logging.warning(f"[process_dial] Failed to log HTTP request: {log_e}")
    else:
        logging.warning(f"[process_dial] No pre-enriched data available")
    
    # ───────── Шаг 3. Формируем текст согласно Пояснению ─────────
    if is_int:
        # Внутренний звонок с обогащением ФИО
        callee_display = callee
        
        # ФИО получателя звонка отключено для устранения блокировок
        
        text = f"🛎️ Внутренний звонок\n ➡️ {callee_display}"
    else:
        # Внешний звонок - ИСПРАВЛЕНО: внутренний номер у ☎️, внешний у 💰
        display = phone if not phone.startswith("+000") else "Номер не определен"
        
        # Обогащаем номер клиента именем если есть
        if enriched_data.get("customer_name"):
            display = f"{display} ({enriched_data['customer_name']})"
        
        # Определяем внутренний номер - из уже обработанных данных
        internal_num = internal_phone or ""

        # Формируем сообщение: внутренний у ☎️, внешний у 💰
        if internal_num:
            # Обогащаем ФИО менеджера (если есть - показываем ФИО (номер), иначе - просто номер)
            manager_name = enriched_data.get("manager_name", "")
            if manager_name and not manager_name.startswith("Доб."):
                # Есть ФИО менеджера - показываем "ФИО (номер)"
                manager_display = f"{manager_name} ({internal_num})"
            else:
                # Нет ФИО - показываем просто номер без "Доб."
                manager_display = internal_num
            
            # Заголовок зависит от CallType
            if call_type == 1:  # Исходящий
                text = f"📞 Исходящий звонок\n☎️{manager_display} ➡️ 💰{display}"
            else:  # Входящий
                text = f"☎️{manager_display} ➡️ 💰{display}"
        else:
            text = f"📞 ➡️ 💰{display}"
            
        # Добавляем информацию о линии (обогащённую)
        if enriched_data.get("line_name"):
            text += f"\n📡{enriched_data['line_name']}"
        elif trunk_info:
            text += f"\nЛиния: {trunk_info}"
            
        # Добавляем историю звонков для входящих
        if call_type != 1:  # Не для исходящих
            last = get_last_call_info(raw_phone)
            if last:
                # Добавляем информацию в формате "Звонил: X раз, Последний раз: дата"
                text += f"\n{last}"

    # Экранируем html-спецсимволы
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.info(f"[process_dial] => chat={chat_id}, text={safe_text!r}")

    # ───────── Шаг 4. Проверяем, нужно ли отправить как комментарий ─────────
    should_comment, reply_to_id = should_send_as_comment(phone_for_grouping, 'dial', chat_id)
    
    # Если предыдущее сообщение было удалено, НЕ отправляем как комментарий
    if should_replace and message_to_delete:
        should_comment = False
        reply_to_id = None
        logging.info(f"[process_dial] Previous message was deleted, sending as standalone message")

    # ───────── Шаг 5. Отправляем сообщение в Telegram ─────────
    try:
        if should_comment and reply_to_id:
            logging.info(f"[process_dial] Sending as comment to message {reply_to_id}")
            sent = await bot.send_message(
                chat_id,
                safe_text,
                reply_to_message_id=reply_to_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
        
        # Логируем отправку Telegram сообщения
        try:
            await call_logger.log_telegram_message(
                enterprise_number=enterprise_number,
                unique_id=uid,
                chat_id=chat_id,
                message_type="dial",
                action="send",
                message_id=sent.message_id,
                message_text=safe_text,
                background=True
            )
            logging.info(f"[process_dial] Logged Telegram message to Call Logger: {sent.message_id}")
        except Exception as log_e:
            logging.warning(f"[process_dial] Failed to log Telegram message: {log_e}")
            
    except BadRequest as e:
        # Логируем ошибку отправки Telegram сообщения
        try:
            await call_logger.log_telegram_message(
                enterprise_number=enterprise_number,
                unique_id=uid,
                chat_id=chat_id,
                message_type="dial",
                action="send",
                message_text=safe_text,
                error=str(e),
                background=True
            )
        except Exception as log_e:
            logging.warning(f"[process_dial] Failed to log Telegram error: {log_e}")
            
        logging.error(f"[process_dial] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # ───────── Шаг 6. Сохраняем в dial_cache ─────────
    dial_cache[uid] = {
        "caller":     raw_phone,
        "extensions": exts,
        "call_type":  call_type,
        "token":      token
    }

    # ───────── Шаг 7. Обновляем состояние системы ─────────
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int, chat_id)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int, chat_id=chat_id)
    
    # Обновляем новый трекер для группировки
    update_phone_tracker(phone_for_grouping, sent.message_id, 'dial', data, chat_id)

    # ───────── Шаг 8. Сохраняем в БД ─────────
    await save_telegram_message(
        sent.message_id,
        "dial",
        token,
        raw_phone,
        callee,
        is_int
    )

    logging.info(f"[process_dial] Successfully sent dial message {sent.message_id} for {phone_for_grouping}")

    # ───────── Шаг 9. Fire-and-forget отправка в Integration Gateway (8020) ─────────
    try:
        token_for_gateway = token
        unique_id_for_gateway = uid
        event_type_for_gateway = "dial"

        async def _dispatch_to_gateway():
            gateway_start_time = time.time()
            gateway_url = "http://localhost:8020/dispatch/call-event"
            
            try:
                payload = {
                    "token": token_for_gateway,
                    "uniqueId": unique_id_for_gateway,
                    "event_type": event_type_for_gateway,
                    "raw": data,
                }
                timeout = aiohttp.ClientTimeout(total=2)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    logging.info(f"[process_dial] gateway dispatch start: uid={unique_id_for_gateway} type={event_type_for_gateway}")
                    resp = await session.post(gateway_url, json=payload)
                    
                    # Логируем успешный запрос к Gateway
                    gateway_duration = (time.time() - gateway_start_time) * 1000
                    await call_logger.log_http_request(
                        enterprise_number=enterprise_number,
                        unique_id=unique_id_for_gateway,
                        method="POST",
                        url=gateway_url,
                        request_data=payload,
                        status_code=resp.status,
                        duration_ms=gateway_duration,
                        background=True
                    )
                    
                    logging.info(f"[process_dial] gateway dispatch done: uid={unique_id_for_gateway} status={resp.status}")
                    
            except Exception as e:
                # Логируем ошибку запроса к Gateway
                gateway_duration = (time.time() - gateway_start_time) * 1000
                await call_logger.log_http_request(
                    enterprise_number=enterprise_number,
                    unique_id=unique_id_for_gateway,
                    method="POST",
                    url=gateway_url,
                    request_data=payload if 'payload' in locals() else None,
                    duration_ms=gateway_duration,
                    error=str(e),
                    background=True
                )
                logging.warning(f"[process_dial] gateway dispatch error: {e}")

        asyncio.create_task(_dispatch_to_gateway())

        # Примечание: уведомления сторонних интеграций (например, U‑ON) теперь пересылаются
        # централизованно через 8020 внутри самого шлюза. Здесь ничего дополнительно не шлём,
        # чтобы не ломать логику retail и не создавать избыточные запросы.
    except Exception as e:
        logging.warning(f"[process_dial] failed to schedule gateway dispatch: {e}")

    return {"status": "sent", "message_id": sent.message_id}
