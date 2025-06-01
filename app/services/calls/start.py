import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    get_last_call_info,
    update_call_pair_message,
    update_hangup_message_map,
    bridge_store,
)

async def process_start(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие Asterisk 'start' (начало звонка):
      1. Логируем входящие данные (DEBUG), чтобы убедиться, под каким ключом приходит номер.
      2. Извлекаем UniqueId и телефон (raw_phone) — учитываем, что в данном событии приходит ключ "Phone".
      3. Формируем текст уведомления: для внутреннего звонка — стрелка между номерами;
         для внешнего — форматируем номер и при наличии истории добавляем информацию о прошлом звонке.
      4. Экранируем символы '<' и '>' в тексте для безопасности HTML.
      5. Пытаемся найти соответствующее hangup-сообщение для reply_to (через get_relevant_hangup_message_id).
      6. Отправляем сообщение в Telegram (с reply_to, если найдено).
      7. Сохраняем message_id в общем хранилище bridge_store, обновляем сопоставления:
         • call_pair_message_map (чтобы новые события можно было reply-to),
         • hangup_message_map (история hangup).
      8. Асинхронно сохраняем запись о сообщении в БД (await save_telegram_message).
    Возвращает {"status": "sent"} при успешной отправке или {"status": "error", "error": ...} в случае ошибки.
    """

    # ───────── Шаг 1. Логируем весь payload, чтобы увидеть точные ключи и значения ─────────
    logging.debug(f"[process_start] RAW DATA = {data!r}")

    # ───────── Шаг 2. Извлечение raw_phone и его форматирование ─────────
    uid = data.get("UniqueId", "")
    # В полученных данных для события start ключ с телефоном называется "Phone"
    raw_phone = data.get("Phone", "") or ""
    phone = format_phone_number(raw_phone)

    exts = data.get("Extensions", [])  # Обычно пуст для события start
    is_int = data.get("CallType", 0) == 2
    callee = exts[0] if exts else ""

    # ───────── Шаг 3. Формируем текст уведомления ─────────
    if is_int:
        # Если внутренний звонок (CallType == 2), просто показываем связь между внутренними номерами
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        # Внешний звонок: проверяем, не +000... (номер неопределён), иначе показываем форматированный
        display = phone if (phone and not phone.startswith("+000")) else "Номер не определен"
        text = f"🛎️ Входящий звонок\n💰 {display}"
        # Добавляем информацию о последнем завершённом звонке, если есть история
        last = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    # Экранируем символы '<' и '>' для безопасности HTML
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    # ───────── Шаг 4. Ищем reply_to и отправляем сообщение ─────────
    try:
        reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_int)
        if reply_id:
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

    # ───────── Шаг 5. Обновляем глобальное состояние ─────────
    bridge_store[uid] = sent.message_id
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    # ───────── Шаг 6. Сохраняем информацию о сообщении в БД ─────────
    await save_telegram_message(
        sent.message_id,
        "start",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )

    return {"status": "sent"}
