
from fastapi import FastAPI, Request
import logging
from telegram import Bot
import asyncio
import phonenumbers
import re
from db import init_db, log_event
import json
from datetime import datetime

app = FastAPI()
init_db()

logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

message_store = {}
dial_store = {}
bridge_store = {}
bridge_phone_index = {}
bridge_seen = set()
dial_cache = {}

def format_phone_number(phone):
    logging.info(f"Original phone: {phone}")
    if len(phone) == 11 and phone.startswith("80"):
        phone = "375" + phone[2:]
    elif len(phone) == 10 and phone.startswith("0"):
        phone = "375" + phone[1:]
    elif phone.startswith("+") and len(phone) > 10:
        return phone
    try:
        if not phone.startswith("+"):
            phone = "+" + phone
        parsed = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        digits = e164[1:]
        country_code = str(parsed.country_code)
        rest = digits[len(country_code):]
        code = rest[:2]
        number = rest[2:]
        return f"+{country_code} ({code}) {number[:3]}-{number[3:5]}-{number[5:]}"
    except Exception:
        logging.error(f"Error in formatting phone number: {phone}")
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    event_type = event_type.lower()
    log_event(event_type, data.get("UniqueId", ""), json.dumps(data))
    logging.info(f"Received event: {event_type}, Data: {data}")

    unique_id = data.get("UniqueId")
    phone = data.get("Phone") or data.get("CallerIDNum") or data.get("CallerIDName") or ""
    formatted_phone = format_phone_number(phone)

    if event_type == "start":
        msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        message_store[unique_id] = sent.message_id
        return {"status": "sent", "event": event_type}

    elif event_type == "dial":
        extensions = data.get("Extensions", [])
        call_type = data.get("CallType")
        dial_cache[unique_id] = {
            "extensions": extensions,
            "call_type": call_type
        }
        if call_type == 1:
            exts = ", ".join(str(e) for e in extensions)
            msg = f"🛎️Исходящий звонок\nМенеджер: {exts} ➡️ 🛎️{formatted_phone}"
        else:
            exts = " ".join(f"🛎️{e}" for e in extensions)
            msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone} ➡️ {exts}"

        if unique_id in message_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_store[unique_id])
                del message_store[unique_id]
            except: pass

        sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        dial_store[phone] = sent.message_id
        return {"status": "sent", "event": event_type}

    elif event_type == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        unique_id = data.get("UniqueId")
        call_status = data.get("CallStatus")

        if "<unknown>" in [caller, connected]:
            return {"status": "ignored", "event": event_type}

        if re.fullmatch(r"\d{3}", caller):
            operator = caller
            client = connected
        else:
            operator = connected
            client = caller

        bridge_key = (client, operator)
        if bridge_key in bridge_seen:
            return {"status": "ignored", "event": event_type}
        bridge_seen.add(bridge_key)

        cached = dial_cache.get(unique_id) or dial_cache.get(client) or {}
        call_type = cached.get("call_type")

        if call_type == 1 and call_status == 2:
            prefix = "✅ Успешный исходящий звонок"
        elif call_type == 0 and call_status == 2:
            prefix = "✅ Успешный входящий звонок"
        else:
            prefix = "🛎️Идет разговор"

        formatted_client = format_phone_number(client)
        msg = f"{prefix}\nАбонент: {formatted_client} ➡️ 🛎️{operator}"

        if client in dial_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=dial_store[client])
                del dial_store[client]
            except: pass

        sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        bridge_store[unique_id] = sent.message_id
        bridge_phone_index[client] = unique_id
        return {"status": "sent", "event": event_type}

    elif event_type == "hangup":
        # Удаление всех связанных сообщений
        for store in [message_store, dial_store, bridge_store]:
            store_copy = dict(store)
            for key, msg_id in store_copy.items():
                if key == unique_id or key == phone:
                    try:
                        await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=msg_id)
                        del store[key]
                    except: pass

        if phone in bridge_phone_index:
            alt_id = bridge_phone_index[phone]
            if alt_id in bridge_store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[alt_id])
                    del bridge_store[alt_id]
                except: pass
            del bridge_phone_index[phone]

        for key in list(bridge_seen):
            if key[0] == phone:
                bridge_seen.remove(key)

        start_time = data.get("StartTime")
        end_time = data.get("EndTime")
        call_status = data.get("CallStatus")
        call_type = data.get("CallType")
        extensions = data.get("Extensions") or []
        cached = dial_cache.get(unique_id, {})
        if not extensions or extensions == [""]:
            extensions = cached.get("extensions", [])

        duration = ""
        if start_time and end_time:
            try:
                start = datetime.fromisoformat(start_time)
                end = datetime.fromisoformat(end_time)
                delta = end - start
                seconds = int(delta.total_seconds())
                duration = f"{seconds // 60:02}:{seconds % 60:02}"
            except: pass

        if call_type == 1 and call_status == 0:
            msg = f"⬆️ ❌ Абонент не ответил\nАбонент: {formatted_phone}\n⌛ {duration} " + " ".join(f"☎️ {e}" for e in extensions)
        elif call_type == 0 and call_status == 1:
            msg = f"⬇️ ❌ Абонент положил трубку\nАбонент: {formatted_phone}\n⌛ {duration}"
        elif call_type == 0 and call_status == 0:
            ext_str = " ".join(f"☎️ {e}" for e in extensions)
            msg = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {formatted_phone}\n⌛ {duration} {ext_str}"
        elif call_status == 2:
            ext = f" ☎️ {extensions[0]}" if extensions else ""
            msg = f"✅ Успешный звонок\nАбонент: {formatted_phone}\n⌛ {duration} 🔈 Запись{ext}"
        else:
            msg = f"❌ Ошибка отображения звонка\nАбонент: {formatted_phone}"

        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        return {"status": "cleared", "event": event_type}
