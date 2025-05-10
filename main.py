from fastapi import FastAPI, Request
import logging
from telegram import Bot
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

message_store = {}        # unique_id -> message_id
dial_store = {}           # unique_id -> message_id
bridge_store = {}         # unique_id -> message_id
dial_cache = {}           # unique_id -> {"extensions": [...], "call_type": n}

def format_phone_number(phone: str) -> str:
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

    # лог в базу и файл
    log_event(event_type, data.get("UniqueId", ""), json.dumps(data))
    logging.info(f"Received event: {event_type}, Data: {data}")

    unique_id = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "")
    formatted_phone = format_phone_number(raw_phone)

    # START
    if event_type == "start":
        msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
            message_store[unique_id] = sent.message_id
        except Exception as e:
            logging.error(f"Failed to send start: {e}")
        return {"status": "sent", "event": event_type}

    # DIAL
    if event_type == "dial":
        call_type = data.get("CallType", 0)
        exts = data.get("Extensions", [])
        if call_type == 1:
            managers = ", ".join(str(e) for e in exts)
            msg = f"🛎️Исходящий звонок\nМенеджер: {managers} ➡️ 🛎️{formatted_phone}"
        else:
            to_str = " ".join(f"🛎️{e}" for e in exts)
            msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone} ➡️ {to_str}"

        # удаляем предыдущий «start»
        if unique_id in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store[unique_id])
                del message_store[unique_id]
            except Exception as e:
                logging.error(f"Failed to delete start: {e}")

        try:
            sent = await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
            dial_store[unique_id] = sent.message_id
            dial_cache[unique_id] = {"extensions": exts, "call_type": call_type}
        except Exception as e:
            logging.error(f"Failed to send dial: {e}")
        return {"status": "sent", "event": event_type}

    # BRIDGE (не изменяем сейчас)
    if event_type == "bridge":
        # ... ваш bridge-код без изменений ...
        return {"status": "ignored", "event": event_type}

    # HANGUP
    if event_type == "hangup":
        call_type = data.get("CallType", 0)
        call_status = int(data.get("CallStatus", -1))
        exts = data.get("Extensions", []) or dial_cache.get(unique_id, {}).get("extensions", [])

        # Удаляем все предыдущие сообщения с этим unique_id
        for store in (message_store, dial_store, bridge_store):
            if unique_id in store:
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, store[unique_id])
                except Exception:
                    pass
                del store[unique_id]

        # вычисляем duration
        duration = ""
        st = data.get("StartTime")
        et = data.get("EndTime")
        if st and et:
            try:
                d_start = datetime.fromisoformat(st)
                d_end = datetime.fromisoformat(et)
                secs = int((d_end - d_start).total_seconds())
                duration = f"{secs//60:02}:{secs%60:02}"
            except:
                pass

        # Формируем текст по трём случаям
        if call_type == 1 and call_status == 0:
            # исходящий + не ответил
            msg = f"⬆️ ❌ Абонент не ответил\nАбонент: {formatted_phone}"
            if duration: msg += f"\n⌛ {duration}"
            if exts:    msg += " " + " ".join(f"☎️ {e}" for e in exts)
        elif call_type == 0 and call_status == 1:
            # входящий + клиент повесил
            msg = f"⬇️ ❌ Абонент положил трубку\nАбонент: {formatted_phone}"
            if duration: msg += f"\n⌛ {duration}"
        elif call_type == 0 and call_status == 0:
            # входящий + не отвечен
            msg = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {formatted_phone}"
            if duration: msg += f"\n⌛ {duration}"
            if exts:    msg += " " + " ".join(f"☎️ {e}" for e in exts)
        elif call_status == 2:
            # успешный звонок
            ext = f" ☎️ {exts[0]}" if exts else ""
            msg = f"✅ Успешный звонок\nАбонент: {formatted_phone}"
            if duration: msg += f"\n⌛ {duration} 🔈 Запись{ext}"
        else:
            # fallback
            msg = f"❌ Завершённый звонок\nАбонент: {formatted_phone}"
            if duration: msg += f"\n⌛ {duration}"

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        except Exception as e:
            logging.error(f"Failed to send hangup: {e}")

        return {"status": "cleared", "event": event_type}

    # остальные события — в «сырых» логах
    raw = f"📞 Asterisk Event: {event_type}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=raw)
    except:
        pass
    return {"status": "sent", "event": event_type}
