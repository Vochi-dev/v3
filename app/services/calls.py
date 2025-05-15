# app/services/calls.py

import asyncio
import logging
import json
from datetime import datetime

from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message

# In-memory stores (будут передаваться из main.py)
dial_cache = {}
bridge_store = {}
active_bridges = {}

# Вспомогательные функции (перенесены из main.py)
def format_phone_number(phone: str) -> str:
    # TODO: реализовать по образцу
    return phone

def is_internal_number(number: str) -> bool:
    # TODO: реализовать по образцу
    return bool(number and number.isdigit() and len(number) <= 4)

def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    # TODO: скопировать логику из main.py
    return None

def update_call_pair_message(caller, callee, message_id, is_internal=False):
    # TODO: скопировать из main.py
    pass

def update_hangup_message_map(caller, callee, message_id, is_internal=False,
                              call_status=-1, call_type=-1, extensions=None):
    # TODO: скопировать из main.py
    pass

def get_last_call_info(external_number: str) -> str:
    # TODO: скопировать из main.py
    return ""

# Обработчики Asterisk-событий
async def process_start(bot: Bot, chat_id: int, data: dict):
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    is_internal = data.get("CallType", 0) == 2

    # Формируем текст сообщения
    if is_internal:
        callee = exts[0] if exts else ""
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        text = f"🛎️ Входящий звонок\n💰 {display}"
        last = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    # Отправляем в Telegram с обработкой ошибок
    try:
        reply_id = get_relevant_hangup_message_id(raw_phone, exts[0] if exts else "", is_internal)
        if reply_id:
            sent = await bot.send_message(chat_id, text, reply_to_message_id=reply_id, parse_mode="HTML")
        else:
            sent = await bot.send_message(chat_id, text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_start] Failed to send: {e.message}. Text: {text!r}")
        return {"status": "error", "error": e.message}

    # Сохраняем в памяти и БД
    bridge_store[uid] = sent.message_id
    save_telegram_message(
        sent.message_id, "start", data.get("Token", ""),
        raw_phone, exts[0] if exts else "", is_internal
    )
    return {"status": "sent"}


async def process_dial(bot: Bot, chat_id: int, data: dict):
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_internal = call_type == 2

    # Удаляем старое «start»-сообщение
    if uid in bridge_store:
        try:
            await bot.delete_message(chat_id, bridge_store.pop(uid))
        except Exception:
            pass

    # Формируем текст
    if is_internal:
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {exts[0] if exts else ''}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        if call_type == 1:
            text = f"⬆️ <b>Набираем номер</b>\n☎️ {', '.join(exts)} ➡️\n💰 {display}"
        else:
            text = f"🛎️ <b>Входящий разговор</b>\n💰 {display} ➡️\n" + \
                   "\n".join(f"☎️ {e}" for e in exts)
        last = get_last_call_info(raw_phone if call_type != 1 else exts[0] if exts else "")
        if last:
            text += f"\n\n{last}"

    # Отправляем и логируем ошибки
    try:
        sent = await bot.send_message(chat_id, text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_dial] Failed to send dial: {e.message}. Text: {text!r}")
        return {"status": "error", "error": e.message}

    # Сохраняем в памяти и БД
    dial_cache[uid] = {
        "caller": raw_phone,
        "extensions": exts,
        "call_type": call_type,
        "token": data.get("Token", "")
    }
    save_telegram_message(
        sent.message_id, "dial", data.get("Token", ""),
        raw_phone, exts[0] if exts else "", is_internal
    )
    return {"status": "sent"}


async def process_bridge(bot: Bot, chat_id: int, data: dict):
    uid = data.get("UniqueId", "")
    # TODO: распарсить caller и connected из data
    # raw parameters example:
    # caller = ...
    # connected = ...
    # Удаление старой «dial»-ной метки
    if uid in dial_cache:
        try:
            await bot.delete_message(chat_id, dial_cache.pop(uid).get("message_id"))
        except Exception:
            pass

    # Формируем текст «bridge» (пример)
    caller = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    text = f"🔗 Соединение\n{caller} ➡️ {connected}"

    # Отправляем и логируем ошибки
    try:
        sent = await bot.send_message(chat_id, text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_bridge] Failed to send bridge: {e.message}. Text: {text!r}")
        return {"status": "error", "error": e.message}

    # Сохраняем в памяти и БД
    bridge_store[uid] = sent.message_id
    save_telegram_message(
        sent.message_id, "bridge", data.get("Token", ""),
        caller, connected, False
    )
    return {"status": "sent"}


async def process_hangup(bot: Bot, chat_id: int, data: dict):
    uid = data.get("UniqueId", "")
    # TODO: добавить логику удаления предыдущих сообщений
    # и расчёт длительности
    caller = data.get("CallerIDNum", "")
    text = f"❌ Завершён звонок {caller}"
    try:
        sent = await bot.send_message(chat_id, text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_hangup] Failed to send hangup: {e.message}. Text: {text!r}")
        return {"status": "error", "error": e.message}

    save_telegram_message(
        sent.message_id, "hangup", data.get("Token", ""),
        caller, "", False
    )
    return {"status": "sent"}


async def create_resend_loop(dial_cache_arg, bridge_store_arg, active_bridges_arg,
                             bot: Bot, chat_id: int):
    """
    Переотправляем сообщения «bridge» каждые 10 секунд.
    """
    while True:
        await asyncio.sleep(10)
        for uid, info in list(active_bridges_arg.items()):
            text = info.get("text", "")
            cli = info.get("cli")
            op = info.get("op")
            is_internal = is_internal_number(cli) and is_internal_number(op)
            reply_id = get_relevant_hangup_message_id(cli, op, is_internal)
            try:
                if uid in bridge_store_arg:
                    await bot.delete_message(chat_id, bridge_store_arg[uid])
                if reply_id:
                    sent = await bot.send_message(chat_id, text,
                                                  reply_to_message_id=reply_id,
                                                  parse_mode="HTML")
                else:
                    sent = await bot.send_message(chat_id, text, parse_mode="HTML")
                bridge_store_arg[uid] = sent.message_id
                save_telegram_message(
                    sent.message_id, "bridge_resend", info.get("token", ""),
                    cli, op, is_internal
                )
            except BadRequest as e:
                logging.error(f"[resend_loop] Failed to resend for {uid}: {e.message}. Text: {text!r}")

