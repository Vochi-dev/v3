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

# Хранилища message_id
message_store = {}    # start
dial_store    = {}    # dial
bridge_store  = {}    # bridge
dial_cache    = {}    # unique_id → {extensions, call_type}

def format_phone_number(phone: str) -> str:
    logging.info(f"Original phone: {phone}")
    if len(phone)==11 and phone.startswith("80"):
        phone = "375"+phone[2:]
    elif len(phone)==10 and phone.startswith("0"):
        phone = "375"+phone[1:]
    elif phone.startswith("+") and len(phone)>10:
        return phone
    try:
        if not phone.startswith("+"):
            phone = "+"+phone
        p = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        d = e164[1:]
        cc = str(p.country_code)
        rest = d[len(cc):]
        code, num = rest[:2], rest[2:]
        return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except:
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId","")
    raw_phone = data.get("Phone","") or data.get("CallerIDNum","")
    phone_fmt = format_phone_number(raw_phone)

    log_event(et, uid, json.dumps(data))
    logging.info(f"{et}: {data}")

    # START
    if et=="start":
        msg = f"🛎️Входящий звонок\nАбонент: {phone_fmt}"
        try:
            s = await bot.send_message(TELEGRAM_CHAT_ID, msg)
            message_store[uid] = s.message_id
        except Exception as e:
            logging.error(e)
        return {"status":"sent"}

    # DIAL
    if et=="dial":
        ct = data.get("CallType",0)
        exts = data.get("Extensions",[])
        if ct==1:
            mgrs = ", ".join(map(str, exts))
            msg = f"🛎️Исходящий звонок\nМенеджер: {mgrs} ➡️ 🛎️{phone_fmt}"
        else:
            to = " ".join(f"🛎️{e}" for e in exts)
            msg = f"🛎️Входящий звонок\nАбонент: {phone_fmt} ➡️ {to}"

        if uid in message_store:
            try: await bot.delete_message(TELEGRAM_CHAT_ID, message_store[uid])
            except: pass
            del message_store[uid]

        try:
            s = await bot.send_message(TELEGRAM_CHAT_ID, msg)
            dial_store[uid] = s.message_id
            dial_cache[uid] = {"extensions":exts, "call_type":ct}
        except Exception as e:
            logging.error(e)
        return {"status":"sent"}

    # BRIDGE — только для того же UniqueId, что был в dial
    if et=="bridge":
        if uid not in dial_cache:
            return {"status":"ignored"}  # не наш вызов

        caller    = data.get("CallerIDNum","")
        connected = data.get("ConnectedLineNum","")
        if "<unknown>" in (caller,connected):
            return {"status":"ignored"}

        # оператор — трёхзначный
        if re.fullmatch(r"\d{3}", caller):
            operator, client = caller, connected
        else:
            operator, client = connected, caller

        fmt_client = format_phone_number(client)
        prefix = "🛎️Идет разговор"
        # если хотим учитывать успешность здесь, можно достать call_status

        msg = f"{prefix}\nАбонент: {fmt_client} ➡️ 🛎️{operator}"

        # удаляем предыдущее dial
        if uid in dial_store:
            try: await bot.delete_message(TELEGRAM_CHAT_ID, dial_store[uid])
            except: pass
            del dial_store[uid]

        try:
            s = await bot.send_message(TELEGRAM_CHAT_ID, msg)
            bridge_store[uid] = s.message_id
        except Exception as e:
            logging.error(e)
        return {"status":"sent"}

    # HANGUP — удаляем ВСЁ по uid, потом отправляем формат
    if et=="hangup":
        # удаляем start,dial,bridge
        for store in (message_store, dial_store, bridge_store):
            if uid in store:
                try: await bot.delete_message(TELEGRAM_CHAT_ID, store[uid])
                except: pass
                del store[uid]

        # duration
        dur=""
        st,etm = data.get("StartTime"), data.get("EndTime")
        try:
            t0=datetime.fromisoformat(st); t1=datetime.fromisoformat(etm)
            s=int((t1-t0).total_seconds())
            dur=f"{s//60:02}:{s%60:02}"
        except: pass

        ct = data.get("CallType",0)
        cs = int(data.get("CallStatus", -1))
        exts = data.get("Extensions",[]) or dial_cache.get(uid,{}).get("extensions",[])

        if ct==1 and cs==0:
            msg = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone_fmt}"
            if dur: msg+=f"\n⌛ {dur}"
            if exts: msg+=" "+" ".join(f"☎️{e}" for e in exts)
        elif ct==0 and cs==1:
            msg = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone_fmt}"
            if dur: msg+=f"\n⌛ {dur}"
        elif ct==0 and cs==0:
            msg = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone_fmt}"
            if dur: msg+=f"\n⌛ {dur}"
            if exts: msg+=" "+" ".join(f"☎️{e}" for e in exts)
        elif cs==2:
            ext = f"☎️{exts[0]}" if exts else ""
            msg = f"✅ Успешный звонок\nАбонент: {phone_fmt}"
            if dur: msg+=f"\n⌛ {dur} 🔈Запись {ext}"
        else:
            msg = f"❌ Завершённый звонок\nАбонент: {phone_fmt}"
            if dur: msg+=f"\n⌛ {dur}"

        try:
            await bot.send_message(TELEGRAM_CHAT_ID, msg)
        except Exception as e:
            logging.error(e)

        return {"status":"cleared"}

    # все прочие — сырые
    raw = "📞 Asterisk Event: "+et+"\n" + "\n".join(f"{k}: {v}" for k,v in data.items())
    try: await bot.send_message(TELEGRAM_CHAT_ID, raw)
    except: pass
    return {"status":"sent"}
