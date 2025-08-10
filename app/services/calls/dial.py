import logging
import asyncio
import aiohttp
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
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

    logging.info(f"[process_dial] RAW DATA = {data!r}")
    logging.info(f"[process_dial] Phone for grouping: {phone_for_grouping}, call_type: {call_type}")

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

    # ───────── Шаг 3. Формируем текст согласно Пояснению ─────────
    if is_int:
        # Внутренний звонок
        text = f"🛎️ Внутренний звонок\n ➡️ {callee}"
    else:
        # Внешний звонок - ИСПРАВЛЕНО: внутренний номер у ☎️, внешний у 💰
        display = phone if not phone.startswith("+000") else "Номер не определен"
        
        # Определяем внутренний номер - из Extensions или CallerIDNum
        internal_num = ""
        if exts:
            # Ищем внутренний номер среди Extensions
            for ext in exts:
                if is_internal_number(ext):
                    internal_num = ext
                    break
        
        if not internal_num:
            # Если не нашли в Extensions, проверяем CallerIDNum
            caller_id = data.get("CallerIDNum", "")
            if is_internal_number(caller_id):
                internal_num = caller_id

        # Формируем сообщение: внутренний у ☎️, внешний у 💰
        if internal_num:
            text = f"☎️{internal_num} ➡️ 💰{display}"
        else:
            text = f"📞 ➡️ 💰{display}"
            
        if trunk_info:
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
            
    except BadRequest as e:
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
                    resp = await session.post(
                        "http://localhost:8020/dispatch/call-event",
                        json=payload,
                    )
                    try:
                        logging.info(f"[process_dial] gateway dispatch done: uid={unique_id_for_gateway} status={resp.status}")
                    except Exception:
                        pass
            except Exception as e:
                logging.warning(f"[process_dial] gateway dispatch error: {e}")

        asyncio.create_task(_dispatch_to_gateway())
    except Exception as e:
        logging.warning(f"[process_dial] failed to schedule gateway dispatch: {e}")

    return {"status": "sent", "message_id": sent.message_id}
