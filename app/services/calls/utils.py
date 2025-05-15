# app/services/calls/utils.py

import asyncio
import logging
from telegram.error import BadRequest
from app.services.events import save_telegram_message

def is_internal_number(number: str) -> bool:
    import re
    return bool(number and re.fullmatch(r"\d{3,4}", number))

def format_phone_number(phone: str) -> str:
    import phonenumbers
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

def update_call_pair_message(caller, callee, message_id, is_internal=False):
    # ... (ваша реализация) ...
    pass

def update_hangup_message_map(caller, callee, message_id,
                              is_internal=False,
                              call_status=-1, call_type=-1,
                              extensions=None):
    # ... (ваша реализация) ...
    pass

def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    # ... (ваша реализация) ...
    pass

def get_last_call_info(external_number: str) -> str:
    # ... (ваша реализация) ...
    return ""

async def create_resend_loop(dial_cache_arg, bridge_store_arg, active_bridges_arg,
                             bot, chat_id: int):
    """
    Переотправляет «bridge» каждые 10 секунд, чтобы держать чат в актуальном состоянии.
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
