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
        msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        message_store[unique_id] = sent.message_id
        logging.info(f"Start message sent: {sent}")

    elif event_type == "dial":
        extensions = data.get("Extensions") or []
        if unique_id in message_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_store[unique_id])
                del message_store[unique_id]
                logging.info(f"Deleted start for UniqueId {unique_id}")
            except Exception:
                pass

        ext_str = " ".join(f"🛎️{ext}" for ext in extensions)
        msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone} ➡️ {ext_str}"
        sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        dial_store[raw_phone] = sent.message_id

    elif event_type == "bridge":
        caller = data.get("CallerIDNum") or ""
        callee = data.get("ConnectedLineNum") or ""

        for number in (caller, callee):
            if looks_like_phone(number):
                phone = number
                break
        else:
            phone = caller or callee

        if not phone:
            return {"status": "no-phone"}

        formatted = format_phone_number(phone)
        short_number = callee if len(callee) <= 4 else caller
        key = (phone, short_number)
        if key in bridge_seen:
            logging.info(f"Ignored repeated bridge for {key}")
            return {"status": "ignored", "event": event_type}

        bridge_seen.add(key)

        if phone in dial_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=dial_store[phone])
                del dial_store[phone]
                logging.info(f"Deleted dial for Phone {phone}")
            except Exception:
                pass

        msg = f"🛎️Идет разговор\nАбонент: {formatted} ➡️ 🛎️{short_number}"
        sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        bridge_store[unique_id] = sent.message_id
        bridge_phone_index[phone] = unique_id

    elif event_type == "hangup":
        phone = data.get("Phone") or data.get("CallerIDNum") or ""
        formatted = format_phone_number(phone)
        extensions = data.get("Extensions") or []

        for store in (message_store, dial_store, bridge_store):
            if unique_id in store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=store[unique_id])
                    del store[unique_id]
                    logging.info(f"Deleted message for UniqueId {unique_id} from {store}")
                except Exception:
                    pass

        if phone in bridge_phone_index:
            alt_id = bridge_phone_index[phone]
            if alt_id in bridge_store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[alt_id])
                    del bridge_store[alt_id]
                    logging.info(f"Deleted bridge for phone {phone} with alt ID {alt_id}")
                except Exception:
                    pass
            del bridge_phone_index[phone]

        for key in list(bridge_seen):
            if key[0] == phone:
                bridge_seen.remove(key)
                logging.info(f"Removed bridge_seen for {key} due to hangup")

        try:
            start_time = data.get("StartTime")
            end_time = data.get("EndTime")
            call_status = data.get("CallStatus")

            duration = ""
            if start_time and end_time:
                try:
                    start = datetime.fromisoformat(start_time)
                    end = datetime.fromisoformat(end_time)
                    delta = end - start
                    seconds = int(delta.total_seconds())
                    duration = f"{seconds // 60:02}:{seconds % 60:02}"
                except Exception:
                    duration = ""

            if str(call_status) == "0":
                msg = f"❌ Неотвеченный вызов\nАбонент: {formatted}"
            elif str(call_status) == "1":
                msg = f"❌ Клиент положил трубку\nАбонент: {formatted}"
            elif str(call_status) == "2":
                ext = f" ☎️ {extensions[0]}" if extensions else ""
                msg = f"✅ Успешный звонок\nАбонент: {formatted}"
                if duration:
                    msg += f"\n⌛ {duration} 🔈 Запись{ext}"
            else:
                msg = f"❌ Вызов завершён\nАбонент: {formatted}"
                if duration:
                    msg += f"\n⌛ {duration}"

            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        except Exception as e:
            logging.error(f"Failed to send formatted hangup: {e}")

        try:
            raw_msg = f"📞 Asterisk Event: hangup\n"
            for k, v in data.items():
                raw_msg += f"{k}: {v}\n"
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=raw_msg)
        except Exception as e:
            logging.error(f"Failed to send raw hangup log: {e}")

        return {"status": "cleared", "event": event_type}

    return {"status": "ok", "event": event_type"}
