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
TELEGRAM_CHAT_ID   = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Хранилища message_id по UniqueId
message_store = {}   # для start
dial_store    = {}   # для dial
bridge_store  = {}   # для bridge
dial_cache    = {}   # кэш: UniqueId -> {"extensions": [...], "call_type": int}

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
        p = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        d = e164[1:]
        cc = str(p.country_code)
        rest = d[len(cc):]
        code, num = rest[:2], rest[2:]
        return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except Exception:
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et   = event_type.lower()
    uid  = data.get("UniqueId", "")
    raw  = data.get("Phone", "") or data.get("CallerIDNum", "")
    phf  = format_phone_number(raw)

    log_event(et, uid, json.dumps(data))
    logging.info(f"{et}: {data}")

    # ===== START =====
    if et == "start":
        txt = f"🛎️Входящий звонок\nАбонент: {phf}"
        try:
            m = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            message_store[uid] = m.message_id
        except Exception as e:
            logging.error(e)
        return {"status": "sent"}

    # ===== DIAL =====
    if et == "dial":
        ct   = data.get("CallType", 0)
        exts = data.get("Extensions", [])
        if ct == 1:
            mgrs = ", ".join(map(str, exts))
            txt = f"🛎️Исходящий звонок\nМенеджер: {mgrs} ➡️ 🛎️{phf}"
        else:
            to = " ".join(f"🛎️{e}" for e in exts)
            txt = f"🛎️Входящий звонок\nАбонент: {phf} ➡️ {to}"

        # удалить старт
        if uid in message_store:
            try: await bot.delete_message(TELEGRAM_CHAT_ID, message_store[uid])
            except: pass
            del message_store[uid]

        try:
            m = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = m.message_id
            dial_cache[uid] = {"extensions": exts, "call_type": ct}
        except Exception as e:
            logging.error(e)
        return {"status": "sent"}

    # ===== BRIDGE =====
    if et == "bridge":
        # показываем только если тот же UID, что в dial
        if uid not in dial_cache:
            return {"status": "ignored"}

        caller    = data.get("CallerIDNum","")
        connected = data.get("ConnectedLineNum","")
        if "<unknown>" in (caller, connected):
            return {"status": "ignored"}

        # оператор — 3 цифры
        if re.fullmatch(r"\d{3}", caller):
            op, client = caller, connected
        else:
            op, client = connected, caller

        txt = f"🛎️Идет разговор\nАбонент: {format_phone_number(client)} ➡️ 🛎️{op}"

        # удалить dial
        if uid in dial_store:
            try: await bot.delete_message(TELEGRAM_CHAT_ID, dial_store[uid])
            except: pass
            del dial_store[uid]

        try:
            m = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            bridge_store[uid] = m.message_id
        except Exception as e:
            logging.error(e)
        return {"status": "sent"}

    # ===== HANGUP =====
    if et == "hangup":
        # удалить все по UID
        for st in (message_store, dial_store, bridge_store):
            if uid in st:
                try: await bot.delete_message(TELEGRAM_CHAT_ID, st[uid])
                except: pass
                del st[uid]

        # duration
        dur = ""
        try:
            s0 = datetime.fromisoformat(data["StartTime"])
            s1 = datetime.fromisoformat(data["EndTime"])
            sec = int((s1-s0).total_seconds())
            dur = f"{sec//60:02}:{sec%60:02}"
        except: pass

        ct   = data.get("CallType",0)
        cs   = int(data.get("CallStatus",-1))
        cache = dial_cache.get(uid, {})
        exts = cache.get("extensions", [])

        # три сценария:
        if ct==1 and cs==0:
            msg = f"⬆️ ❌ Абонент не ответил\nАбонент: {phf}"
            if dur: msg+=f"\n⌛ {dur}"
            if exts: msg+=" " + " ".join(f"☎️{e}" for e in exts)
        elif ct==0 and cs==1:
            msg = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phf}"
            if dur: msg+=f"\n⌛ {dur}"
        elif ct==0 and cs==0:
            msg = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phf}"
            if dur: msg+=f"\n⌛ {dur}"
            if exts: msg+=" " + " ".join(f"☎️{e}" for e in exts)
        else:
            # остальные — успешный или прочее
            if cs==2:
                e0 = exts[0] if exts else ""
                msg = f"✅ Успешный звонок\nАбонент: {phf}"
                if dur: msg+=f"\n⌛ {dur} 🔈Запись {e0}"
            else:
                msg = f"❌ Завершённый звонок\nАбонент: {phf}"
                if dur: msg+=f"\n⌛ {dur}"

        try:
            await bot.send_message(TELEGRAM_CHAT_ID, msg)
        except Exception as e:
            logging.error(e)

        return {"status":"cleared"}

    # ===== ALL OTHER EVENTS =====
    raw = "📞 Asterisk Event: " + et + "\n" + "\n".join(f"{k}: {v}" for k,v in data.items())
    try: await bot.send_message(TELEGRAM_CHAT_ID, raw)
    except: pass
    return {"status":"sent"}
