# app/services/calls/utils.py

import asyncio
import logging
from datetime import datetime
from collections import defaultdict
import re
import phonenumbers
from telegram.error import BadRequest

from app.services.events import save_telegram_message

# ───────── In-memory stores ─────────
dial_cache       = {}
bridge_store     = {}
active_bridges   = {}

# для историй
call_pair_message_map = {}
hangup_message_map    = defaultdict(list)


# ───────── Утилиты для номеров ─────────
def is_internal_number(number: str) -> bool:
    return bool(number and re.fullmatch(r"\d{3,4}", number))

def format_phone_number(phone: str) -> str:
    if not phone:
        return phone
    if is_internal_number(phone):
        return phone
    if not phone.startswith("+"):
        phone = "+" + phone
    try:
        parsed = phonenumbers.parse(phone, None)
        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except Exception:
        return phone


# ───────── Обновление истории ─────────
def update_call_pair_message(caller, callee, message_id, is_internal=False):
    if is_internal:
        key = tuple(sorted([caller, callee]))
    else:
        key = (caller,)
    call_pair_message_map[key] = message_id
    return key

def update_hangup_message_map(caller, callee, message_id,
                              is_internal=False,
                              call_status=-1, call_type=-1,
                              extensions=None):
    rec = {
        'message_id': message_id,
        'caller':      caller,
        'callee':      callee,
        'timestamp':   datetime.now().isoformat(),
        'call_status': call_status,
        'call_type':   call_type,
        'extensions':  extensions or []
    }
    hangup_message_map[caller].append(rec)
    if is_internal:
        hangup_message_map[callee].append(rec)
    # оставляем не более 5
    hangup_message_map[caller]   = hangup_message_map[caller][-5:]
    if is_internal:
        hangup_message_map[callee] = hangup_message_map[callee][-5:]


def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    if is_internal:
        hist = hangup_message_map.get(caller, []) + hangup_message_map.get(callee, [])
    else:
        hist = hangup_message_map.get(caller, [])
    if not hist:
        return None
    hist.sort(key=lambda x: x['timestamp'], reverse=True)
    return hist[0]['message_id']


def get_last_call_info(external_number: str) -> str:
    hist = hangup_message_map.get(external_number, [])
    if not hist:
        return ""
    last = sorted(hist, key=lambda x: x['timestamp'], reverse=True)[0]
    ts   = datetime.fromisoformat(last['timestamp'])
    ts   = ts.replace(hour=(ts.hour + 3) % 24)  # GMT+3
    when = ts.strftime("%d.%m.%Y %H:%M")
    status = last['call_status']
    ctype  = last['call_type']
    icon   = "✅" if status == 2 else "❌"
    if ctype == 0:  # входящий
        return f"🛎️ Последний: {when}\n{icon}"
    else:
        return f"⬆️ Последний: {when}\n{icon}"


async def create_resend_loop(dial_cache_arg, bridge_store_arg, active_bridges_arg,
                             bot, chat_id: int):
    """
    Переотправляет незакрытые bridge-сообщения каждые 10 сек.
    """
    while True:
        await asyncio.sleep(10)
        for uid, info in list(active_bridges_arg.items()):
            text    = info.get("text", "")
            cli     = info.get("cli")
            op      = info.get("op")
            is_int  = is_internal_number(cli) and is_internal_number(op)
            reply_id= get_relevant_hangup_message_id(cli, op, is_int)

            safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
            logging.debug(f"[resend_loop] => chat={chat_id}, text={safe_text!r}")

            try:
                if uid in bridge_store_arg:
                    await bot.delete_message(chat_id, bridge_store_arg[uid])
                if reply_id:
                    sent = await bot.send_message(
                        chat_id, safe_text,
                        reply_to_message_id=reply_id,
                        parse_mode="HTML"
                    )
                else:
                    sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
                bridge_store_arg[uid] = sent.message_id
                update_hangup_message_map(cli, op, sent.message_id, is_int)
                save_telegram_message(
                    sent.message_id, "bridge_resend",
                    info.get("token", ""),
                    cli, op, is_int
                )
            except BadRequest as e:
                logging.error(f"[resend_loop] failed for {uid}: {e}. text={safe_text!r}")
