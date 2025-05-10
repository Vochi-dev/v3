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

TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
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
    unique_id = data.get("UniqueId")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("CallerIDName") or ""
    formatted = format_phone_number(raw_phone)

    log_event(event_type, unique_id, json.dumps(data))
    logging.info(f"Received event: {event_type}, Data: {data}")

    if event_type == "dial":
        dial_cache[unique_id] = data

    if event_type == "hangup":
        for store in [message_store, dial_store, bridge_store]:
            if unique_id in store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=store[unique_id])
                    del store[unique_id]
                except Exception as e:
                    logging.error(f"Failed to delete message: {e}")

        if raw_phone in bridge_phone_index:
            alt_id = bridge_phone_index[raw_phone]
            if alt_id in bridge_store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[alt_id])
                    del bridge_store[alt_id]
                except Exception as e:
                    logging.error(f"Failed to delete alt bridge: {e}")
            del bridge_phone_index[raw_phone]

        for key in list(bridge_seen):
            if key[0] == raw_phone:
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
            except Exception:
                pass

        cached_dial = dial_cache.get(unique_id, {})
        extensions = data.get("Extensions") or cached_dial.get("Extensions", [])
        extensions = [ext for ext in extensions if ext]  # Удаляем пустые строки

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

        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        return {"status": "cleared", "event": event_type}

    return {"status": "ignored", "event": event_type}
