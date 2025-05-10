from fastapi import FastAPI, Request
import logging
from telegram import Bot
import asyncio
import phonenumbers
import re

app = FastAPI()

logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Хранилища
message_store = {}           # UniqueId -> message_id (start)
dial_store = {}              # phone_number -> message_id (dial)
bridge_store = {}            # UniqueId -> message_id (bridge)
bridge_phone_index = {}      # phone_number -> UniqueId (для bridge)
bridge_seen = set()          # (client_number, operator_number)

def format_phone_number(phone):
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

    logging.info(f"Received event: {event_type}, Data: {data}")

    unique_id = data.get("UniqueId")
    linked_id = data.get("LinkedId") or unique_id
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("CallerIDName") or ""
    formatted_phone = format_phone_number(raw_phone)

    # START
    if event_type == "start":
        message = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
            if unique_id:
                message_store[unique_id] = sent.message_id
        except Exception as e:
            logging.error(f"Failed to send start: {e}")
        return {"status": "sent", "event": event_type}

    # DIAL
    elif event_type == "dial":
        extensions = data.get("Extensions", [])
        extensions_str = " ".join(f"🛎️{ext}" for ext in extensions if isinstance(ext, (str, int)))
        message = f"🛎️Входящий звонок\nАбонент: {formatted_phone} ➡️ {extensions_str}"

        if unique_id and unique_id in message_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_store[unique_id])
                del message_store[unique_id]
                logging.info(f"Deleted start for UniqueId {unique_id}")
            except Exception as e:
                logging.error(f"Failed to delete start: {e}")

        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
            if raw_phone:
                dial_store[raw_phone] = sent.message_id
        except Exception as e:
            logging.error(f"Failed to send dial: {e}")
        return {"status": "sent", "event": event_type}

    # BRIDGE
    elif event_type == "bridge":
        caller_number = data.get("CallerIDNum", "")
        connected_number = data.get("ConnectedLineNum", "")
        unique_id = data.get("UniqueId")

        if re.fullmatch(r"\d{3}", caller_number):
            operator_number = caller_number
            client_number = connected_number
        else:
            operator_number = connected_number
            client_number = caller_number

        bridge_key = (client_number, operator_number)
        if bridge_key in bridge_seen:
            logging.info(f"Ignored repeated bridge for {bridge_key}")
            return {"status": "ignored", "event": event_type}

        formatted_client = format_phone_number(client_number)
        message = f"🛎️Идет разговор\nАбонент: {formatted_client} ➡️ 🛎️{operator_number}"

        if client_number in dial_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=dial_store[client_number])
                del dial_store[client_number]
                logging.info(f"Deleted dial for Phone {client_number}")
            except Exception as e:
                logging.error(f"Failed to delete dial: {e}")

        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
            if unique_id:
                bridge_store[unique_id] = sent.message_id
            if client_number:
                bridge_phone_index[client_number] = unique_id
        except Exception as e:
            logging.error(f"Failed to send bridge: {e}")

        bridge_seen.add(bridge_key)
        return {"status": "sent", "event": event_type}

    # HANGUP
    elif event_type == "hangup":
        phone = data.get("Phone") or data.get("CallerIDNum") or ""
        unique_id = data.get("UniqueId", "")
        formatted = format_phone_number(phone)

        # Удалить start
        if unique_id in message_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=message_store[unique_id])
                del message_store[unique_id]
                logging.info(f"Deleted start for UniqueId {unique_id}")
            except Exception as e:
                logging.error(f"Failed to delete start in hangup: {e}")

        # Удалить dial
        if phone in dial_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=dial_store[phone])
                del dial_store[phone]
                logging.info(f"Deleted dial for Phone {phone}")
            except Exception as e:
                logging.error(f"Failed to delete dial in hangup: {e}")

        # Удалить bridge по UniqueId
        if unique_id in bridge_store:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=bridge_store[unique_id])
                del bridge_store[unique_id]
                logging.info(f"Deleted bridge by UniqueId {unique_id}")
            except Exception as e:
                logging.error(f"Failed to delete bridge by UniqueId: {e}")

        # Удалить bridge по номеру, если другой UniqueId
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

        # Чистим bridge_seen
        to_remove = [key for key in bridge_seen if key[0] == phone]
        for key in to_remove:
            bridge_seen.remove(key)
            logging.info(f"Removed bridge_seen for {key} due to hangup")

        # Сообщение о завершении
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"❌ Вызов завершён\nАбонент: {formatted}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Failed to send hangup message: {e}")

        return {"status": "cleared", "event": event_type}

    # Другое
    else:
        message = f"📞 *Asterisk Event: {event_type}*\n"
        for key, value in data.items():
            if isinstance(value, str) and looks_like_phone(value):
                value = format_phone_number(value)
            message += f"{key}: {value}\n"
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to send other: {e}")
        return {"status": "sent", "event": event_type}

