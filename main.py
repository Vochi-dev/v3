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
TELEGRAM_CHAT_ID    = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

message_store      = {}
dial_store         = {}
bridge_store       = {}
bridge_phone_index = {}
bridge_seen        = set()
dial_cache         = {}

def format_phone_number(phone):
    logging.info(f"Original phone: {phone}")
    if len(phone)==11 and phone.startswith("80"):
        phone="375"+phone[2:]
    elif len(phone)==10 and phone.startswith("0"):
        phone="375"+phone[1:]
    elif phone.startswith("+") and len(phone)>10:
        return phone
    try:
        if not phone.startswith("+"):
            phone="+"+phone
        p=phonenumbers.parse(phone,None)
        e164=phonenumbers.format_number(p,phonenumbers.PhoneNumberFormat.E164)
        d=e164[1:]
        cc=str(p.country_code)
        rest=d[len(cc):]
        code=rest[:2]
        num=rest[2:]
        return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except:
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId","")
    raw = data.get("Phone") or data.get("CallerIDNum") or ""
    phone = format_phone_number(raw)

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, raw={raw}")

    # START
    if et=="start":
        txt=f"🛎️ Входящий звонок\nАбонент: {phone}"
        sent=await bot.send_message(TELEGRAM_CHAT_ID, txt)
        message_store[uid]=sent.message_id
        return {"status":"sent"}

    # DIAL
    if et=="dial":
        ct=int(data.get("CallType",0))
        exts=data.get("Extensions",[]) or []
        if ct==1:
            txt=f"🛎️ Исходящий звонок\nМенеджер: {', '.join(map(str,exts))} ➡️ {phone}"
        else:
            txt=f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ "+" ".join(f"🛎️{e}" for e in exts)
        if uid in message_store:
            await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
        sent=await bot.send_message(TELEGRAM_CHAT_ID, txt)
        dial_store[uid]=sent.message_id
        # Сохраняем расширения и тип по UID
        dial_cache[uid]={"call_type":ct,"extensions":exts}
        return {"status":"sent"}

    # BRIDGE
    if et=="bridge":
        caller=data.get("CallerIDNum","")
        conn=data.get("ConnectedLineNum","")
        cs=int(data.get("CallStatus",0))
        if "<unknown>" in (caller,conn):
            return {"status":"ignored"}
        if re.fullmatch(r"\d{3}",caller):
            op,cli=caller,conn
        else:
            op,cli=conn,caller
        key=(cli,op)
        if key in bridge_seen:
            return {"status":"ignored"}
        if uid not in dial_cache:
            return {"status":"ignored"}
        # удаляем DIAL
        await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid,0))
        ct=dial_cache[uid]["call_type"]
        if ct==1 and cs==2:
            pre="✅ Успешный исходящий звонок"
        elif ct==0 and cs==2:
            pre="✅ Успешный входящий звонок"
        else:
            pre="🛎️ Идет разговор"
        txt=f"{pre}\nАбонент: {format_phone_number(cli)} ➡️ 🛎️{op}"
        sent=await bot.send_message(TELEGRAM_CHAT_ID, txt)
        bridge_store[uid]=sent.message_id
        bridge_phone_index[cli]=uid
        bridge_seen.add(key)
        return {"status":"sent"}

    # HANGUP
    if et=="hangup":
        # удаляем всё по UID
        for store in (message_store, dial_store, bridge_store):
            if uid in store:
                await bot.delete_message(TELEGRAM_CHAT_ID, store.pop(uid))
        # готовим данные
        st=data.get("StartTime"); etm=data.get("EndTime")
        cs=int(data.get("CallStatus",0)); ct=int(data.get("CallType",0))
        cache=dial_cache.get(uid, {})
        exts=cache.get("extensions",[])
        dur=""
        try:
            s=datetime.fromisoformat(st); e=datetime.fromisoformat(etm)
            sec=int((e-s).total_seconds()); dur=f"{sec//60:02}:{sec%60:02}"
        except: pass

        # форматируем сообщение
        if ct==1 and cs==0:
            m=f"⬆️ ❌ Абонент не ответил\nАбонент: {phone}"
        elif ct==0 and cs==1:
            m=f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone}"
        elif ct==0 and cs==0:
            m=f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone}"
        elif cs==2:
            prefix = "✅ Успешный входящий звонок" if ct==0 else "✅ Успешный исходящий звонок"
            m=f"{prefix}\nАбонент: {phone}"
        else:
            m=f"❌ Завершённый звонок\nАбонент: {phone}"

        if dur:
            m+=f"\n⌛ {dur}"
        # добавляем номера в нужных кейсах
        if (ct,cs) in ((0,0),(1,0)):
            for e in exts:
                m+=f" ☎️ {e}"
        if cs==2 and exts:
            m+=f" 🔈 Запись ☎️ {exts[0]}"

        await bot.send_message(TELEGRAM_CHAT_ID, m)
        return {"status":"cleared"}

    # остальные события
    txt="📞 Event "+et+"\n" + "\n".join(f"{k}: {v}" for k,v in data.items())
    await bot.send_message(TELEGRAM_CHAT_ID, txt)
    return {"status":"sent"}
