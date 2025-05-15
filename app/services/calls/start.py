# app/services/calls/start.py

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
    hangup_message_map,
)

# Нам нужно локально хранить bridge_store для start
from . import utils  # чтобы обратиться к utils.hangup_message_map
from . import utils as _utils

# Вспомогательная точка хранения bridge_store
try:
    bridge_store = _utils.bridge_store
except AttributeError:
    # Если bridge_store ещё не определён
    bridge_store = {}
    _utils.bridge_store = bridge_store  # создаём в utils

async def process_start(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'start':
    — формирует текст,
    — ищет reply-to по hangup-истории,
    — отправляет сообщение,
    — сохраняет state и в БД.
    """
    uid       = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    phone     = format_phone_number(raw_phone)
    exts      = data.get("Extensions", [])
    is_int    = data.get("CallType", 0) == 2
    callee    = exts[0] if exts else ""

    # Формируем текст
    if is_int:
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        text    = f"🛎️ Входящий звонок\n💰 {display}"
        last    = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    # Экранирование
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    # Отправляем, возможно reply_to
    try:
        reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_int)
        if reply_id:
            sent = await bot.send_message(
                chat_id, safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_start] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем в-memory и историю
    bridge_store[uid] = sent.message_id
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    # Сохраняем в БД
    save_telegram_message(
        sent.message_id,
        "start",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )

    return {"status": "sent"}
