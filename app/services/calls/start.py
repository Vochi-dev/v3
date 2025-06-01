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
    Обрабатывает событие Asterisk 'start':
      1. Печатаем весь payload в stdout (для отладки).
      2. Извлекаем UniqueId и телефон (raw_phone) — в этом JSON ключ называется "Phone".
      3. Формируем текст: 🛎️ Входящий звонок + форматированный номер или "Номер не определен".
      4. Экранируем '<' и '>' и отправляем в Telegram (reply_to, если есть предыдущий hangup).
      5. Сохраняем message_id в bridge_store, обновляем history-структуры.
      6. await save_telegram_message для записи в БД.
    """

    # ───────── Шаг 1. Вывод в stdout всего payload ─────────
    print(f"[process_start] RAW DATA = {data!r}")

    # ───────── Шаг 2. Извлечение raw_phone и форматирование ─────────
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    phone = format_phone_number(raw_phone)

    exts = data.get("Extensions", [])
    is_int = data.get("CallType", 0) == 2
    callee = exts[0] if exts else ""

    # ───────── Шаг 3. Формируем текст уведомления ─────────
    if is_int:
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if (phone and not phone.startswith("+000")) else "Номер не определен"
        text = f"🛎️ Входящий звонок\n💰 {display}"
        last = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")

    # ───────── Шаг 3a. Выводим сформированный текст ─────────
    print(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    # ───────── Шаг 4. Отправка в Telegram ─────────
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

    # ───────── Шаг 6. Сохраняем в БД ─────────
    await save_telegram_message(
        sent.message_id,
        "start",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )

    return {"status": "sent"}
