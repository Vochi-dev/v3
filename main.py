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
        logging.error(f"Error formatting phone: {phone}")
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    event_type = event_type.lower()

    log_event(event_type, data.get("UniqueId", ""), json.dumps(data))
    logging.info(f"Received event: {event_type}, Data: {data}")

    unique_id = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "")
    formatted_phone = format_phone_number(raw_phone)

    # START
    if event_type == "start":
        msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone}"
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, msg)
            message_store[unique_id] = sent.message_id
        except Exception as e:
            logging.error(f"Failed to send start: {e}")
        return {"status": "sent"}

    # DIAL
    if event_type == "dial":
        call_type = data.get("CallType", 0)
        exts = data.get("Extensions", [])
        if call_type == 1:
            mgrs = ", ".join(str(e) for e in exts)
            msg = f"🛎️Исходящий звонок\nМенеджер: {mgrs} ➡️ 🛎️{formatted_phone}"
        else:
            to_str = " ".join(f"🛎️{e}" for e in exts)
            msg = f"🛎️Входящий звонок\nАбонент: {formatted_phone} ➡️ {to_str}"

        # удаляем старт
        if unique_id in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store[unique_id])
            except: pass
            del message_store[unique_id]

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, msg)
            dial_store[unique_id] = sent.message_id
            dial_cache[unique_id] = {"extensions": exts, "call_type": call_type}
        except Exception as e:
            logging.error(f"Failed to send dial: {e}")
        return {"status": "sent"}

    # BRIDGE
    if event_type == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        # игнорируем если неизвестно
        if "<unknown>" in (caller, connected):
            return {"status": "ignored"}

        # определяем кто оператор, кто клиент
        if re.fullmatch(r"\d{3}", caller):
            operator, client = caller, connected
        else:
            operator, client = connected, caller

        formatted_client = format_phone_number(client)
        msg = f"🛎️Идет разговор\nАбонент: {formatted_client} ➡️ 🛎️{operator}"

        # удаляем предыдущий dial
        if unique_id in dial_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, dial_store[unique_id])
            except: pass
            del dial_store[unique_id]

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, msg)
            bridge_store[unique_id] = sent.message_id
        except Exception as e:
            logging.error(f"Failed to send bridge: {e}")
        return {"status": "sent"}

    # HANGUP
    if event_type == "hangup":
        call_type   = data.get("CallType", 0)
        call_status = int(data.get("CallStatus", -1))
        cached      = dial_cache.get(unique_id, {})
        exts        = data.get("Extensions", []) or cached.get("extensions", [])

        # удаляем все по unique_id
        for st in (message_store, dial_store, bridge_store):
            if unique_id in st:
                try: await bot.delete_message(TELEGRAM_CHAT_ID, st[unique_id])
                except: pass
                del st[unique_id]

        # duration
        dur = ""
        stime = data.get("StartTime"); etime = data.get("EndTime")
        if stime and etime:
            try:
                t0 = datetime.fromisoformat(stime)
                t1 = datetime.fromisoformat(etime)
                s = int((t1-t0).total_seconds())
                dur = f"{s//60:02}:{s%60:02}"
            except: pass

        # формируем текст
        if call_type==1 and call_status==0:
            msg = f"⬆️ ❌ Абонент не ответил\nАбонент: {formatted_phone}"
            if dur: msg += f"\n⌛ {dur}"
            if exts: msg += " " + " ".join(f"☎️ {e}" for e in exts)
        elif call_type==0 and call_status==1:
            msg = f"⬇️ ❌ Абонент положил трубку\nАбонент: {formatted_phone}"
            if dur: msg += f"\n⌛ {dur}"
        elif call_type==0 and call_status==0:
            msg = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {formatted_phone}"
            if dur: msg += f"\n⌛ {dur}"
            if exts: msg += " " + " ".join(f"☎️ {e}" for e in exts)
        elif call_status==2:
            ext = f" ☎️ {exts[0]}" if exts else ""
            msg = f"✅ Успешный звонок\nАбонент: {formatted_phone}"
            if dur: msg += f"\n⌛ {dur} 🔈 Запись{ext}"
        else:
            msg = f"❌ Завершённый звонок\nАбонент: {formatted_phone}"
            if dur: msg += f"\n⌛ {dur}"

        try:
            await bot.send_message(TELEGRAM_CHAT_ID, msg)
        except Exception as e:
            logging.error(f"Failed to send hangup: {e}")

        return {"status": "cleared"}

    # остальные — сырые логи
    raw = f"📞 Asterisk Event: {event_type}\n" + "\n".join(f"{k}: {v}" for k,v in data.items())
    try:
        await bot.send_message(TELEGRAM_CHAT_ID, raw)
    except: pass
    return {"status": "sent"}
