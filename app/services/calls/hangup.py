# app/services/calls/hangup.py

import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    update_call_pair_message,
    update_hangup_message_map
)
from .start import bridge_store
from .dial import dial_cache
from .bridge import bridge_store as bridge_msg_store, dial_cache as bridge_dial_cache
from . import utils

async def process_hangup(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'hangup':
    — удаляет все сообщения по UID,
    — рассчитывает длительность,
    — формирует итоговый текст,
    — отправляет (возможно reply_to),
    — обновляет историю и сохраняет в БД.
    """
    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "") or ""
    exts      = data.get("Extensions", []) or []
    connected = data.get("ConnectedLineNum", "") or ""
    is_int    = bool(exts and exts[0].isdigit() and len(exts[0]) <= 4)
    callee    = exts[0] if exts else connected

    # Удаляем start/dial/bridge по UID
    for store in (bridge_store, dial_cache, bridge_msg_store):
        store.pop(uid, None)
    # и из active_bridges
    utils.active_bridges.pop(uid, None)

    # Рассчитываем duration
    dur = ""
    try:
        start = data.get("StartTime")
        end   = data.get("EndTime")
        from datetime import datetime
        secs  = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
        dur   = f"{secs//60:02}:{secs%60:02}"
    except:
        pass

    # Формируем display-номер
    phone   = format_phone_number(caller)
    display = phone if not phone.startswith("+000") else "Номер не определен"
    cs = int(data.get("CallStatus", -1))
    ct = int(data.get("CallType", -1))

    # Формируем итоговый текст
    if is_int:
        m = ("✅ Успешный внутренний звонок\n" if cs == 2 else "❌ Абонент не ответил\n")
        m += f"{caller} ➡️ {callee}\n⌛ {dur}"
    else:
        if ct == 1 and cs == 0:
            m = f"⬆️ ❌ Абонент не ответил\n💰 {display}"
        elif cs == 2:
            m = f"✅ Завершённый звонок\n💰 {display}\n⌛ {dur}"
        else:
            m = f"❌ Завершённый звонок\n💰 {display}\n⌛ {dur}"

    safe_text = m.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_hangup] => chat={chat_id}, text={safe_text!r}")

    # Ищем reply_to: сначала hangup-history, потом pair-map
    reply_id = get_relevant_hangup_message_id(caller, callee, is_int)
    if not reply_id:
        reply_id = call_pair_message_map.get((caller,)) or call_pair_message_map.get(tuple(sorted([caller, callee])))

    # Отправляем
    try:
        if reply_id:
            sent = await bot.send_message(
                chat_id, safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_hangup] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Обновляем историю
    update_call_pair_message(caller, callee, sent.message_id, is_int)
    update_hangup_message_map(caller, callee, sent.message_id, is_int, cs, ct, exts)

    # Сохраняем в БД
    save_telegram_message(
        sent.message_id,
        "hangup",
        data.get("Token", ""),
        caller,
        callee,
        is_int
    )
    return {"status": "sent"}
