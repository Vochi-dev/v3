from fastapi import FastAPI, Request
import logging
from telegram import Bot
import phonenumbers
import re
from db import init_db, log_event
import json
from datetime import datetime
import asyncio

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
bridge_seen = set()
dial_cache = {}
dial_phone_to_uid = {}
active_bridges = {}
hangup_reply_map = {}  # хранит message_id по ключу пар участников

def format_phone_number(phone: str) -> str:
    logging.info(f"Original phone: {phone}")
    if not phone:
        return phone
    # внутр. номера оставляем как есть
    if re.match(r"^\d{3,4}$", phone):
        return phone
    # нормализуем внешние
    if len(phone) == 11 and phone.startswith("80"):
        phone = "375" + phone[2:]
    elif len(phone) == 10 and phone.startswith("0"):
        phone = "375" + phone[1:]
    try:
        if not phone.startswith("+"):
            phone = "+" + phone
        parsed = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        cc = str(parsed.country_code)
        rest = e164[1+len(cc):]
        return f"+{cc} ({rest[:2]}) {rest[2:5]}-{rest[5:7]}-{rest[7:]}"
    except:
        return phone

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    ct = int(data.get("CallType", 0))

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, phone={phone}, data={data}")

    if et in {"init", "ping", "test", "healthcheck"}:
        return {"status": "ignored"}

    # вспомогательный ключ — для внутренних двух цифр, для внешних (phone,)
    def mk_key(a, b=None):
        if b:
            return tuple(sorted([a, b]))
        return (a,)

    # START
    if et == "start":
        if ct == 2 or (re.match(r"^\d{3,4}$", raw_phone) and exts and re.match(r"^\d{3,4}$", exts[0])):
            # внутренний
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {exts[0]}"
            key = mk_key(raw_phone, exts[0])
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone}"
            key = mk_key(raw_phone)
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        message_store[uid] = sent.message_id
        hangup_reply_map[key] = sent.message_id
        return {"status": "sent"}

    # DIAL
    if et == "dial":
        if not raw_phone or not exts: 
            return {"status": "ignored"}
        if uid in dial_store:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
        if ct == 2 and re.match(r"^\d{3,4}$", raw_phone) and re.match(r"^\d{3,4}$", exts[0]):
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {exts[0]}"
            key = mk_key(raw_phone, exts[0])
        elif ct == 1:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(exts)} ➡️ {phone}"
            key = mk_key(raw_phone)
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ " + " ".join(f"🛎️{e}" for e in exts)
            key = mk_key(raw_phone)
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        dial_store[uid] = sent.message_id
        dial_cache[uid] = {"call_type": ct, "extensions": exts, "caller": raw_phone}
        dial_phone_to_uid[raw_phone] = uid
        hangup_reply_map[key] = sent.message_id
        return {"status": "sent"}

    # BRIDGE
    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))
        orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(connected)
        if not orig_uid:
            return {"status": "ignored"}
        dial_info = dial_cache.get(orig_uid, {})
        a = dial_info.get("caller", caller)
        b = dial_info.get("extensions",[connected])[0]
        key = mk_key(a, b)
        if key in bridge_seen:
            return {"status": "ignored"}
        # удаляем DIAL
        await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        # текст
        if re.match(r"^\d{3,4}$", a) and re.match(r"^\d{3,4}$", b):
            txt = f"⏱ Идет внутренний разговор\n{a} ➡️ {b}"
        else:
            pre = "✅ Успешный исходящий звонок" if ct==1 and status==2 else "✅ Успешный входящий звонок" if ct==0 and status==2 else "⏱ Идет разговор"
            fa = format_phone_number(a)
            fb = format_phone_number(b)
            txt = f"{pre}\nАбонент: {fa} ➡️ 🛎️{b}"
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        bridge_store[orig_uid] = sent.message_id
        bridge_seen.add(key)
        hangup_reply_map[key] = sent.message_id
        return {"status": "sent"}

    # HANGUP
    if et == "hangup":
        # удаляем все предыдущие уведомления по UID
        for st in (message_store, dial_store, bridge_store):
            if uid in st:
                await bot.delete_message(TELEGRAM_CHAT_ID, st.pop(uid))
        # параметры
        raw_exts = data.get("Extensions", [])
        exts_clean = [e for e in raw_exts if e]
        di = dial_cache.get(uid, {})
        caller = di.get("caller", raw_phone)
        callee = exts_clean[0] if exts_clean else di.get("extensions",[""])[0]
        key = mk_key(caller, callee) if callee else mk_key(caller)
        # длительность
        dur = ""
        try:
            s = datetime.fromisoformat(data.get("StartTime"))
            e = datetime.fromisoformat(data.get("EndTime"))
            secs = int((e-s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except:
            pass
        # формируем сообщение
        if re.match(r"^\d{3,4}$", caller) and callee:
            # внутренний
            if data.get("CallStatus")=="2":
                m = f"✅ Успешный внутренний звонок\n{caller} ➡️ {callee}\n⌛ {dur} 🔈 Запись"
            else:
                m = f"❌ Абонент не ответил\n{caller} ➡️ {callee}\n⌛ {dur}"
        else:
            phone_fmt = format_phone_number(caller)
            cs = int(data.get("CallStatus", -1))
            ct = int(data.get("CallType", -1))
            if ct==1 and cs==0:
                m = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone_fmt}\n⌛ {dur}"
            elif ct==0 and cs==1:
                m = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone_fmt}\n⌛ {dur}"
            elif ct==0 and cs==0:
                m = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone_fmt}\n⌛ {dur}"
            elif ct==0 and cs==2:
                m = f"⬇️ ✅ Успешный входящий звонок\nАбонент: {phone_fmt}\n⌛ {dur} 🔈 Запись"
            elif ct==1 and cs==2:
                m = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {phone_fmt}\n⌛ {dur} 🔈 Запись"
            else:
                m = f"❌ Завершённый звонок\nАбонент: {phone_fmt}\n⌛ {dur}"
        # отправляем с reply_to, если есть
        rid = hangup_reply_map.get(key)
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, m, reply_to_message_id=rid)
        except:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
        hangup_reply_map[key] = sent.message_id
        return {"status": "cleared"}

    # все прочие
    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k,v in data.items())
    await bot.send_message(TELEGRAM_CHAT_ID, txt)
    return {"status":"sent"}
