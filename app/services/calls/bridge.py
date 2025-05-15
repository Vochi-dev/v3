# app/services/calls/bridge.py

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
)
from .start import bridge_store
from .dial import dial_cache

# Для сбора незавершённых мостов
from .utils import hangup_message_map
from collections import OrderedDict

async def process_bridge(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'bridge':
    — удаляем связанный dial (если есть),
    — формируем текст для внутреннего или внешнего разговора,
    — сохраняем в active_bridges для повторной отправки,
    — отправляем и сохраняем историю.
    """
    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    is_int    = format_phone_number(caller) == caller and caller.isdigit() and len(caller) <= 4 \
                and connected.isdigit() and len(connected) <= 4

    # Удаляем «dial», чтобы не было старого сообщения
    if uid in dial_cache:
        dial_cache.pop(uid)
        try:
            await bot.delete_message(chat_id, bridge_store.get(uid, 0))
        except Exception:
            pass

    # Формируем текст
    if is_int:
        text = f"⏱ Идет внутренний разговор\n{caller} ➡️ {connected}"
    else:
        status = int(data.get("CallStatus", 0))
        pre    = "✅ Успешный разговор" if status == 2 else "⬇️ 💬 <b>Входящий разговор</b>"
        cli    = format_phone_number(caller)
        cal    = format_phone_number(connected)
        text   = f"{pre}\n☎️ {cli} ➡️ 💰 {cal}"
        last   = get_last_call_info(connected)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_bridge] => chat={chat_id}, text={safe_text!r}")

    # Отправляем
    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_bridge] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем состояние и историю
    bridge_store[uid] = sent.message_id
    update_call_pair_message(caller, connected, sent.message_id, is_int)
    update_hangup_message_map(caller, connected, sent.message_id, is_int)

    # Трекер незакрытых мостов для resend-loop
    from . import calls  # если нужен доступ к active_bridges
    try:
        calls.active_bridges[uid] = {
            "text": safe_text,
            "cli":  caller,
            "op":   connected,
            "token": data.get("Token", "")
        }
    except:
        pass

    # Сохраняем в БД
    save_telegram_message(
        sent.message_id,
        "bridge",
        data.get("Token", ""),
        caller,
        connected,
        is_int
    )

    return {"status": "sent"}
