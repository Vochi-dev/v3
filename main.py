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

# ========== Логирование ==========
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ========== Telegram Bot ==========
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ========== Хранилища сообщений ==========
message_store   = {}  # UID -> message_id (start)
dial_store      = {}  # UID -> message_id (dial)
bridge_store    = {}  # UID -> message_id (bridge)
bridge_seen     = set()  # set of tuple(sorted([caller,callee]))
dial_cache      = {}  # UID -> dict(call_type, extensions, caller)
dial_phone_to_uid = {}  # raw_phone -> UID
hangup_reply_map = {}  # tuple(sorted([caller,callee])) or (phone,) -> message_id

# ========== Вспомогательные функции ==========

def is_internal_number(num: str) -> bool:
    return bool(re.fullmatch(r"\d{3,4}", num))

def format_phone_number(phone: str) -> str:
    """Формат E.164 -> +375 (xx) xxx-xx-xx, внутренние остаются"""
    if not phone:
        return phone
    if is_internal_number(phone):
        return phone
    # Привести к 375...
    if phone.startswith("80") and len(phone) == 11:
        phone = "375" + phone[2:]
    elif phone.startswith("0") and len(phone) == 10:
        phone = "375" + phone[1:]
    if not phone.startswith("+"):
        phone = "+" + phone
    try:
        parsed = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        digits = e164[1:]  # без '+'
        cc = str(parsed.country_code)
        rest = digits[len(cc):]
        return f"+{cc} ({rest[:2]}) {rest[2:5]}-{rest[5:7]}-{rest[7:]}"
    except:
        return phone

def make_pair_key(a: str, b: str=None):
    """Ключ для пары участников hangup: упорядоченный кортеж или единичный"""
    if b:
        return tuple(sorted([a, b]))
    return (a,)

# ========== Основная логика ==========
@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
    phone_fmt = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    ct = int(data.get("CallType", 0))

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event={et}, UID={uid}, raw={raw_phone}, data={data}")

    # Игнорируем:
    if et in {"init", "ping", "test", "healthcheck"}:
        return {"status": "ignored"}

    # --- START ---
    if et == "start":
        # internal?
        if ct == 2 or (is_internal_number(raw_phone) and exts and is_internal_number(exts[0])):
            a, b = raw_phone, exts[0]
            txt = f"🛎️ Внутренний звонок\n{a} ➡️ {b}"
            key = make_pair_key(a, b)
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone_fmt}"
            key = make_pair_key(raw_phone)
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        message_store[uid] = sent.message_id
        hangup_reply_map[key] = sent.message_id
        return {"status": "sent"}

    # --- DIAL ---
    if et == "dial":
        if not raw_phone or not exts:
            return {"status": "ignored"}
        # удаляем предыдущее dial
        if uid in dial_store:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
        # internal?
        if ct == 2 and is_internal_number(raw_phone) and is_internal_number(exts[0]):
            a, b = raw_phone, exts[0]
            txt = f"🛎️ Внутренний звонок\n{a} ➡️ {b}"
            key = make_pair_key(a, b)
        elif ct == 1:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(exts)} ➡️ {phone_fmt}"
            key = make_pair_key(raw_phone)
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone_fmt} ➡️ " + " ".join(f"🛎️{e}" for e in exts)
            key = make_pair_key(raw_phone)
        # удаляем start
        if uid in message_store:
            await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        dial_store[uid] = sent.message_id
        dial_cache[uid] = {"call_type": ct, "extensions": exts, "caller": raw_phone}
        dial_phone_to_uid[raw_phone] = uid
        hangup_reply_map[key] = sent.message_id
        return {"status": "sent"}

    # --- BRIDGE ---
    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))
        orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(connected)
        if not orig_uid:
            return {"status": "ignored"}
        di = dial_cache.get(orig_uid, {})
        a = di.get("caller", caller)
        b = di.get("extensions", [connected])[0]
        key = make_pair_key(a, b)
        if key in bridge_seen:
            return {"status": "ignored"}
        # удаляем dial
        await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))

        # internal?
        if is_internal_number(a) and is_internal_number(b):
            txt = f"⏱ Идет внутренний разговор\n{a} ➡️ {b}"
        else:
            pre = ("✅ Успешный исходящий звонок" if ct == 1 and status == 2
                   else "✅ Успешный входящий звонок" if ct == 0 and status == 2
                   else "⏱ Идет разговор")
            fa = format_phone_number(a if ct == 0 else b)
            fb = format_phone_number(b if ct == 0 else a)
            txt = f"{pre}\nАбонент: {fa} ➡️ 🛎️{fb}"
        sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
        bridge_store[orig_uid] = sent.message_id
        bridge_seen.add(key)
        hangup_reply_map[key] = sent.message_id
        return {"status": "sent"}

    # --- HANGUP ---
    if et == "hangup":
        # удаляем все прошлые по этому UID
        for st in (message_store, dial_store, bridge_store):
            if uid in st:
                await bot.delete_message(TELEGRAM_CHAT_ID, st.pop(uid))
        # определяем пару
        di = dial_cache.get(uid, {})
        caller = di.get("caller", raw_phone)
        ext_clean = [e for e in data.get("Extensions", []) if e]
        callee = ext_clean[0] if ext_clean else di.get("extensions", [""])[0]
        key = make_pair_key(caller, callee) if callee else make_pair_key(caller)
        # длительность
        dur = ""
        try:
            s = datetime.fromisoformat(data.get("StartTime"))
            e = datetime.fromisoformat(data.get("EndTime"))
            secs = int((e - s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except:
            pass
        # текст
        if is_internal_number(caller) and callee:
            # внутренний
            if data.get("CallStatus") == "2":
                m = f"✅ Успешный внутренний звонок\n{caller} ➡️ {callee}\n⌛ {dur} 🔈 Запись"
            else:
                m = f"❌ Абонент не ответил\n{caller} ➡️ {callee}\n⌛ {dur}"
        else:
            fmt = format_phone_number(caller)
            cs = int(data.get("CallStatus", -1))
            ct_ = int(data.get("CallType", -1))
            if ct_ == 1 and cs == 0:
                m = f"⬆️ ❌ Абонент не ответил\nАбонент: {fmt}\n⌛ {dur}"
            elif ct_ == 0 and cs == 1:
                m = f"⬇️ ❌ Абонент положил трубку\nАбонент: {fmt}\n⌛ {dur}"
            elif ct_ == 0 and cs == 0:
                m = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {fmt}\n⌛ {dur}"
            elif ct_ == 0 and cs == 2:
                m = f"⬇️ ✅ Успешный входящий звонок\nАбонент: {fmt}\n⌛ {dur} 🔈 Запись"
            elif ct_ == 1 and cs == 2:
                m = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {fmt}\n⌛ {dur} 🔈 Запись"
            else:
                m = f"❌ Завершённый звонок\nАбонент: {fmt}\n⌛ {dur}"
        # отправка с reply_to, если есть
        rid = hangup_reply_map.get(key)
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, m, reply_to_message_id=rid)
        except:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
        hangup_reply_map[key] = sent.message_id
        return {"status": "cleared"}

    # --- Остальные события ---
    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    await bot.send_message(TELEGRAM_CHAT_ID, txt)
    return {"status": "sent"}
