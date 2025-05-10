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

message_store = {}
dial_store = {}
bridge_store = {}
bridge_phone_index = {}
bridge_seen = set()

def format_phone_number(phone):
    # Логирование перед обработкой
    logging.info(f"Original phone: {phone}")

    # Если номер длиной 10 символов, заменяем первую цифру на "375"
    if len(phone) == 10 and phone.startswith("0"):
        phone = "375" + phone[1:]
    
    # Если номер начинается с "+" и его длина больше 10, но начинается с "8", обрубаем "8" и добавляем "375"
    elif phone.startswith("+8") and len(phone) == 12:
        logging.info(f"Processing +8 number: {phone}")
        phone = "+375" + phone[2:]
    
    # Если номер уже начинается с "+" и является международным, пропускаем его
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

    logging.info(f"Received event: {event_type}, Data: {data}")

    unique_id = data.get("UniqueId")
    linked_id = data.get("LinkedId") or unique_id
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("CallerIDName") or ""
    formatted_phone = format_phone_number(raw_phone)

    # Логирование информации о номере телефона
    logging.info(f"Raw phone: {raw_phone}, Formatted phone: {formatted_phone}")

    if event_type == "start":
        # Логирование для проверки наличия номера телефона
        if not formatted_phone:
            logging.error(f"Start event: No phone number found. Data: {data}")

        message = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        logging.info(f"Sending start message: {message}")

        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
            logging.info(f"Start message sent: {sent}")
            if unique_id:
                message_store[unique_id] = sent.message_id
        except Exception as e:
            logging.error(f"Failed to send start: {e}")
        return {"status": "sent", "event": event_type}

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

    elif event_type == "hangup":
        phone = data.get("Phone") or data.get("CallerIDNum") or ""
        unique_id = data.get("UniqueId", "")
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

        to_remove = [key for key in bridge_seen if key[0] == phone]
        for key in to_remove:
            bridge_seen.remove(key)
            logging.info(f"Removed bridge_seen for {key} due to hangup")

        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"❌ Вызов завершён\nАбонент: {formatted}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Failed to send hangup message: {e}")

        return {"status": "cleared", "event": event_type}

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
