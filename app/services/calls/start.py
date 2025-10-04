import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from app.utils.logger_client import call_logger
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    get_last_call_info,
    update_call_pair_message,
    update_hangup_message_map,
    bridge_store,
    bridge_store_by_chat,
    # Новые функции для группировки событий
    get_phone_for_grouping,
    should_send_as_comment,
    should_replace_previous_message,
    update_phone_tracker,
)

async def process_start(bot: Bot, chat_id: int, data: dict):
    """
    Модернизированный обработчик события 'start' (17.01.2025):
    - Использует новую систему группировки по номеру телефона
    - Применяет форматы сообщений из файла "Пояснение"
    - Поддерживает отправку комментариев к предыдущим сообщениям
    """

    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

    # Получаем номер для группировки событий
    phone_for_grouping = get_phone_for_grouping(data)
    
    # ───────── Шаг 1. Вывод в stdout всего payload ─────────
    logging.info(f"[process_start] RAW DATA = {data!r}")
    logging.info(f"[process_start] Phone for grouping: {phone_for_grouping}")

    # ───────── Шаг 2. Извлечение данных ─────────
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int = call_type == 2
    callee = exts[0] if exts else ""
    token = data.get("Token", "")
    
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
                    logging.info(f"[process_start] Resolved Token '{token}' -> enterprise '{enterprise_number}'")
                else:
                    logging.warning(f"[process_start] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_start] Failed to resolve enterprise_number: {e}")

    # ───────── Логирование start события в Call Logger ─────────
    try:
        await call_logger.log_call_event(
            enterprise_number=enterprise_number,
            unique_id=uid,
            event_type="start",
            event_data=data,
            phone_number=phone,
            chat_id=chat_id
        )
        logging.info(f"[process_start] Logged start event to Call Logger: {uid}")
    except Exception as e:
        logging.warning(f"[process_start] Failed to log start event: {e}")

    # ───────── Шаг 3. Формируем текст согласно Пояснению ─────────
    if is_int:
        # Внутренние звонки обычно не имеют start события
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        # Входящий звонок - используем формат из Пояснения
        display = phone if (phone and not phone.startswith("+000")) else "Номер не определен"
        
        # Базовый формат для start события
        text = f"💰{display} ➡️ Приветствие"
        
        # Добавляем информацию о линии, если есть Token
        if token:
            # Пытаемся определить название линии по токену
            trunk_info = data.get("Trunk", "")
            if trunk_info:
                text += f"\nЛиния: {trunk_info}"
        
        # Добавляем историю звонков
        last = get_last_call_info(raw_phone)
        if last:
            # Извлекаем информацию из истории для формата "Звонил: X раз"
            # Пока используем базовую логику, можно будет улучшить
            text += f"\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")

    # ───────── Шаг 3a. Выводим сформированный текст ─────────
    logging.info(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    # ───────── Шаг 4. Проверяем, нужно ли отправить как комментарий ─────────
    should_comment, reply_to_id = should_send_as_comment(phone_for_grouping, 'start', chat_id)
    
    # ───────── Шаг 5. Отправка в Telegram ─────────
    try:
        if should_comment and reply_to_id:
            logging.info(f"[process_start] Sending as comment to message {reply_to_id}")
            sent = await bot.send_message(
                chat_id,
                safe_text,
                reply_to_message_id=reply_to_id,
                parse_mode="HTML"
            )
        else:
            # Проверяем старую логику reply_to для совместимости
            reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_int, chat_id)
            if reply_id and not should_comment:
                sent = await bot.send_message(
                    chat_id,
                    safe_text,
                    reply_to_message_id=reply_id,
                    parse_mode="HTML"
                )
            else:
                sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_start] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # ───────── Шаг 6. Обновляем состояние системы ─────────
    bridge_store_by_chat[chat_id][uid] = sent.message_id
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int, chat_id)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int, chat_id=chat_id)
    
    # Обновляем новый трекер для группировки
    update_phone_tracker(phone_for_grouping, sent.message_id, 'start', data, chat_id)

    # ───────── Шаг 7. Сохраняем в БД ─────────
    await save_telegram_message(
        sent.message_id,
        "start",
        token,
        raw_phone,
        callee,
        is_int
    )

    logging.info(f"[process_start] Successfully sent start message {sent.message_id} for {phone_for_grouping}")
    return {"status": "sent", "message_id": sent.message_id}
