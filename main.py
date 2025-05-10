from fastapi import FastAPI, Request
import logging
from telegram import Bot
import asyncio
import phonenumbers
import re
from db import init_db, log_event
import json

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
        message = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            message_store[unique_id] = sent.message_id
            logging.info(f"Start message sent: {sent}")
        except Exception as e:
            logging.error(f"Failed to send start: {e}")
        return {"status": "sent", "event": event_type}

    elif event_type == "dial":
        extensions = data.get("Extensions", [])
        extensions_str = " ".join(f"🛎️{ext}" for ext in extensions if isinstance(ext, (str, int)))
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
        except Exception as e:
            logging.error(f"Failed to send dial: {e}")
        return {"status": "sent", "event": event_type}

    elif event_type == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        unique_id = data.get("UniqueId")

        if re.fullmatch(r"\d{3}", caller):
            operator = caller
            client = connected
        else:
            operator = connected
            client = caller

        bridge_key = (client, operator)
        if bridge_key in bridge_seen:
            logging.info(f"Ignored repeated bridge for {bridge_key}")
            return {"status": "ignored", "event": event_type}

        formatted_client = format_phone_number(client)
        message = f"🛎️Идет разговор\nАбонент: {formatted_client} ➡️ 🛎️{operator}"

        if client in dial_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=dial_store[client])
                del dial_store[client]
                logging.info(f"Deleted dial for Phone {client}")
            except Exception as e:
                logging.error(f"Failed to delete dial: {e}")

        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            if unique_id:
                bridge_store[unique_id] = sent.message_id
            if client:
                bridge_phone_index[client] = unique_id
        except Exception as e:
            logging.error(f"Failed to send bridge: {e}")

        bridge_seen.add(bridge_key)
        return {"status": "sent", "event": event_type}

    elif event_type == "hangup":
        phone = data.get("Phone") or data.get("CallerIDNum") or ""
        unique_id = data.get("UniqueId")
        formatted = format_phone_number(phone)

        if unique_id in message_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_store[unique_id])
                del message_store[unique_id]
                logging.info(f"Deleted start for UniqueId {unique_id}")
            except Exception as e:
                logging.error(f"Failed to delete start in hangup: {e}")

        if phone in dial_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=dial_store[phone])
                del dial_store[phone]
                logging.info(f"Deleted dial for Phone {phone}")
            except Exception as e:
                logging.error(f"Failed to delete dial in hangup: {e}")

        if unique_id in bridge_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[unique_id])
                del bridge_store[unique_id]
                logging.info(f"Deleted bridge by UniqueId {unique_id}")
            except Exception as e:
                logging.error(f"Failed to delete bridge by UniqueId: {e}")

        if phone in bridge_phone_index:
            alt_id = bridge_phone_index[phone]
            if alt_id in bridge_store:
                try:
                    await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[alt_id])
                    del bridge_store[alt_id]
                    logging.info(f"Deleted bridge by phone {phone} and alt UniqueId {alt_id}")
                except Exception as e:
                    logging.error(f"Failed to delete bridge by phone: {e}")
            del bridge_phone_index[phone]

        for key in list(bridge_seen):
            if key[0] == phone:
                bridge_seen.remove(key)
                logging.info(f"Removed bridge_seen for {key} due to hangup")

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"❌ Вызов завершён\nАбонент: {formatted}")
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

    else:
        message = f"📞 *Asterisk Event: {event_type}*\n"
        for k, v in data.items():
            if isinstance(v, str) and looks_like_phone(v):
                v = format_phone_number(v)
            message += f"{k}: {v}\n"
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        except Exception as e:
            logging.error(f"Failed to send other: {e}")
        return {"status": "sent", "event": event_type}
