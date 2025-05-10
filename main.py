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

def looks_like_phone(value):
    if not isinstance(value, str):
        return False
    digits = re.sub(r"\D", "", value)
    return len(digits) >= 9

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    event_type = event_type.lower()

    log_event(event_type, data.get("UniqueId", ""), json.dumps(data))
    logging.info(f"Received event: {event_type}, Data: {data}")

    unique_id = data.get("UniqueId")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("CallerIDName") or ""
    formatted_phone = format_phone_number(raw_phone)

    logging.info(f"Raw phone: {raw_phone}, Formatted phone: {formatted_phone}")

    if event_type == "start":
        message = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            message_store[unique_id] = sent.message_id
            logging.info(f"Start message sent: {sent}")
        except Exception as e:
            logging.error(f"Failed to send start: {e}")
        return {"status": "sent", "event": event_type}

    elif event_type == "dial":
        call_type = data.get("CallType")
        extensions = data.get("Extensions", [])
        if call_type == 1:
            exts = ", ".join(str(ext) for ext in extensions)
            message = f"🛎️Исходящий звонок\nМенеджер: {exts} ➡️ 🛎️{formatted_phone}"
        else:
            extensions_str = " ".join(f"🛎️{ext}" for ext in extensions)
            message = f"🛎️Входящий звонок\nАбонент: {formatted_phone} ➡️ {extensions_str}"

        if unique_id in message_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_store[unique_id])
                del message_store[unique_id]
                logging.info(f"Deleted start for UniqueId {unique_id}")
            except Exception as e:
                logging.error(f"Failed to delete start: {e}")

        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            if raw_phone:
                dial_store[raw_phone] = sent.message_id
                dial_cache[unique_id] = {
                    "extensions": extensions,
                    "call_type": call_type
                }
        except Exception as e:
            logging.error(f"Failed to send dial: {e}")
        return {"status": "sent", "event": event_type}

    elif event_type == "hangup":
        phone = data.get("Phone") or data.get("CallerIDNum") or ""
        unique_id = data.get("UniqueId")
        formatted = format_phone_number(phone)

        for store in [message_store, dial_store, bridge_store]:
            store_copy = dict(store)
            for k, v in store_copy.items():
                if k == unique_id or k == phone:
                    try:
                        await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=v)
                        del store[k]
                        logging.info(f"Deleted message with key {k}")
                    except Exception as e:
                        logging.error(f"Failed to delete message {k}: {e}")

        if phone in bridge_phone_index:
            alt_id = bridge_phone_index[phone]
            if alt_id in bridge_store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[alt_id])
                    del bridge_store[alt_id]
                except Exception as e:
                    logging.error(f"Failed to delete bridge by alt id: {e}")
            del bridge_phone_index[phone]

        for key in list(bridge_seen):
            if key[0] == phone:
                bridge_seen.remove(key)

        start_time = data.get("StartTime")
        end_time = data.get("EndTime")
        call_status = data.get("CallStatus")
        call_type = data.get("CallType")
        duration = ""
        if start_time and end_time:
            try:
                start = datetime.fromisoformat(start_time)
                end = datetime.fromisoformat(end_time)
                delta = end - start
                seconds = int(delta.total_seconds())
                duration = f"{seconds // 60:02}:{seconds % 60:02}"
            except:
                duration = ""

        cached = dial_cache.get(unique_id, {})
        extensions = data.get("Extensions", []) or cached.get("extensions", [])

        if call_type == 1 and call_status == 0:
            msg = f"⬆️ ❌ Абонент не ответил\nАбонент: {formatted}"
            if duration:
                msg += f"\n⌛ {duration}"
            if extensions:
                msg += " " + " ".join(f"☎️ {ext}" for ext in extensions)
        elif call_type == 0 and call_status == 1:
            msg = f"⬇️ ❌ Абонент положил трубку\nАбонент: {formatted}"
            if duration:
                msg += f"\n⌛ {duration}"
        elif call_type == 0 and call_status == 0:
            msg = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {formatted}"
            if duration:
                msg += f"\n⌛ {duration}"
            if extensions:
                msg += " " + " ".join(f"☎️ {ext}" for ext in extensions)
        elif call_status == 2:
            ext = f" ☎️ {extensions[0]}" if extensions else ""
            msg = f"✅ Успешный звонок\nАбонент: {formatted}\n⌛ {duration} 🔈 Запись{ext}"
        else:
            msg = f"❌ Ошибка отображения звонка\nАбонент: {formatted}"

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        except Exception as e:
            logging.error(f"Failed to send formatted hangup: {e}")

        return {"status": "cleared", "event": event_type}
