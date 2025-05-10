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

message_store      = {}  # start messages by UID
dial_store         = {}  # dial messages by UID
bridge_store       = {}  # bridge messages by UID
bridge_phone_index = {}  # map phone→UID for bridge
bridge_seen        = set()
dial_cache         = {}  # cache call_type & extensions by UID

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
        e164   = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        digits = e164[1:]
        cc     = str(parsed.country_code)
        rest   = digits[len(cc):]
        code   = rest[:2]
        num    = rest[2:]
        return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except Exception:
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data      = await request.json()
    et        = event_type.lower()
    uid       = data.get("UniqueId","")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or ""
    phone     = format_phone_number(raw_phone)

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, raw={raw_phone}")

    # START
    if et == "start":
        txt = f"🛎️ Входящий звонок\nАбонент: {phone}"
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        message_store[uid] = sent.message_id
        return {"status":"sent"}

    # DIAL
    if et == "dial":
        ct   = data.get("CallType", 0)
        exts = data.get("Extensions", [])
        if ct == 1:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(map(str,exts))} ➡️ {phone}"
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ " + " ".join(f"🛎️{e}" for e in exts)
        # delete START
        if uid in message_store:
            await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        dial_store[uid] = sent.message_id
        dial_cache[uid] = {"call_type": ct, "extensions": exts}
        return {"status":"sent"}

    # BRIDGE
    if et == "bridge":
        caller    = data.get("CallerIDNum","")
        connected = data.get("ConnectedLineNum","")
        status    = data.get("CallStatus", 0)
        # ignore unknown
        if "<unknown>" in (caller, connected):
            return {"status":"ignored"}
        # assign operator/client
        if re.fullmatch(r"\d{3}", caller):
            op, cli = caller, connected
        else:
            op, cli = connected, caller
        key = (cli, op)
        if key in bridge_seen:
            return {"status":"ignored"}
        # must match previous dial UID
        if uid not in dial_cache:
            return {"status":"ignored"}
        # delete DIAL
        await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid, 0))
        ct = dial_cache[uid]["call_type"]
        # choose prefix
        if   ct==1 and status==2: pre="✅ Успешный исходящий звонок"
        elif ct==0 and status==2: pre="✅ Успешный входящий звонок"
        else:                     pre="🛎️ Идет разговор"
        txt = f"{pre}\nАбонент: {format_phone_number(cli)} ➡️ 🛎️{op}"
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        bridge_store[uid] = sent.message_id
        bridge_phone_index[cli] = uid
        bridge_seen.add(key)
        return {"status":"sent"}

    # HANGUP
    if et == "hangup":
        # delete all by UID
        for store in (message_store, dial_store, bridge_store):
            if uid in store:
                await bot.delete_message(TELEGRAM_CHAT_ID, store.pop(uid))
        # compute duration
        st   = data.get("StartTime"); etime = data.get("EndTime")
        cs   = data.get("CallStatus"); ct = data.get("CallType")
        exts = data.get("Extensions",[]) or dial_cache.get(uid,{}).get("extensions",[])
        dur  = ""
        try:
            s = datetime.fromisoformat(st)
            e = datetime.fromisoformat(etime)
            secs = int((e-s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except Exception:
            pass

        # build final message
        if   ct==1 and cs==0:
            m = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone}"
            if dur: m += f"\n⌛ {dur}"
            if exts: m += "".join(f" ☎️ {e}" for e in exts)
        elif ct==0 and cs==1:
            m = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone}"
            if dur: m += f"\n⌛ {dur}"
        elif ct==0 and cs==0:
            m = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone}"
            if dur: m += f"\n⌛ {dur}"
            if exts: m += "".join(f" ☎️ {e}" for e in exts)
        elif cs==2 and ct==0:
            m = f"⬇️ ✅ Успешный входящий звонок\nАбонент: {phone}"
            m += f"\n⌛ {dur} 🔈 Запись"
            if exts: m += f" ☎️ {exts[0]}"
        elif cs==2 and ct==1:
            m = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {phone}"
            m += f"\n⌛ {dur} 🔈 Запись"
            if exts: m += f" ☎️ {exts[0]}"
        else:
            m = f"❌ Завершенный звонок\nАбонент: {phone}"
            if dur: m += f"\n⌛ {dur}"

        await bot.send_message(TELEGRAM_CHAT_ID, m)
        return {"status":"cleared"}

    # OTHER
    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k,v in data.items())
    await bot.send_message(TELEGRAM_CHAT_ID, txt)
    return {"status":"sent"}


