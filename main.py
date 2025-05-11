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
bridge_phone_index = {}
bridge_seen = set()
dial_cache = {}
dial_phone_to_uid = {}
active_bridges = {}
hangup_reply_map = {}  # Хранит message_id для пар {caller, callee}

def format_phone_number(phone: str) -> str:
    logging.info(f"Original phone: {phone}")
    if not phone:
        return phone
    if len(phone) == 11 and phone.startswith("80"):
        phone = "375" + phone[2:]
    elif len(phone) == 10 and phone.startswith("0"):
        phone = "375" + phone[1:]
    try:
        if not phone.startswith("+"):
            phone = "+" + phone
        parsed = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        digits = e164[1:]
        cc = str(parsed.country_code)
        rest = digits[len(cc):]
        code = rest[:2]
        num = rest[2:]
        return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except Exception:
        return phone

def is_internal_number(number: str) -> bool:
    """Проверяет, является ли номер внутренним (3-4 цифры)."""
    return number and re.match(r"^\d{3,4}$", number)

@app.on_event("startup")
async def start_bridge_resender():
    async def resend_loop():
        while True:
            await asyncio.sleep(5)
            for uid in list(active_bridges.keys()):
                msg_id = bridge_store.get(uid)
                if not msg_id:
                    continue
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, msg_id)
                except:
                    pass
                try:
                    txt = active_bridges[uid]["text"]
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
                    bridge_store[uid] = sent.message_id
                except:
                    pass
    asyncio.create_task(resend_loop())

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
    phone = format_phone_number(raw_phone)
    call_type = int(data.get("CallType", 0))

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, raw={raw_phone}, data={data}")

    if et in {"init", "ping", "test", "healthcheck"}:
        return {"status": "ignored"}

    # Определяем, внутренний ли звонок
    is_internal = call_type == 2 or (is_internal_number(raw_phone) and all(is_internal_number(e) for e in data.get("Extensions", [])))

    if et == "start":
        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {data.get('Extensions', [''])[0]}"
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone}"
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            message_store[uid] = sent.message_id
        except:
            pass
        return {"status": "sent"}

    if et == "dial":
        exts = data.get("Extensions", [])
        if not raw_phone or not exts:
            return {"status": "ignored"}

        if uid in dial_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
            except:
                pass

        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {exts[0]}"
        else:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(map(str, exts))} ➡️ {phone}" if call_type == 1 else f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ " + " ".join(f"🛎️{e}" for e in exts)

        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except:
                pass
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": call_type, "extensions": exts, "caller": raw_phone}
            dial_phone_to_uid[raw_phone] = uid
        except:
            pass
        return {"status": "sent"}

    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))

        if caller == "<unknown>" and connected == "<unknown>":
            return {"status": "ignored"}

        if is_internal_number(caller):
            op, cli = caller, connected
        else:
            op, cli = connected, caller

        if not cli:
            return {"status": "ignored"}

        orig_uid = dial_phone_to_uid.get(cli) or uid
        if not orig_uid:
            return {"status": "ignored"}

        key = (cli, op)
        if key in bridge_seen:
            return {"status": "ignored"}

        try:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        except:
            pass

        if is_internal:
            txt = f"⏱ Идет внутренний разговор\n{cli} ➡️ {op}"
        else:
            pre = "✅ Успешный исходящий звонок" if call_type == 1 and status == 2 else "✅ Успешный входящий звонок" if call_type == 0 and status == 2 else "🛎️ Идет разговор"
            formatted_cli = format_phone_number(cli)
            txt = f"{pre}\nАбонент: {formatted_cli} ➡️ 🛎️{op}" if call_type == 0 else f"{pre}\n{op} ➡️ 🛎️{formatted_cli}"

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            bridge_store[orig_uid] = sent.message_id
            bridge_phone_index[cli] = orig_uid
            bridge_seen.add(key)
            active_bridges[orig_uid] = {"text": txt, "cli": cli, "op": op}
        except:
            pass
        return {"status": "sent"}

    if et == "hangup":
        if not raw_phone:
            return {"status": "ignored"}

        for store in (message_store, dial_store, bridge_store):
            if uid in store:
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, store.pop(uid))
                except:
                    pass
        active_bridges.pop(uid, None)

        st = data.get("StartTime")
        etme = data.get("EndTime")
        cs = int(data.get("CallStatus", -1))
        ct = int(data.get("CallType", -1))
        raw_exts = data.get("Extensions", [])
        exts = [e for e in raw_exts if e and str(e).strip()]
        if not exts:
            exts = dial_cache.get(uid, {}).get("extensions", [])

        caller = raw_phone
        callee = exts[0] if exts else dial_cache.get(uid, {}).get("caller", "")

        dur = ""
        try:
            s = datetime.fromisoformat(st)
            e = datetime.fromisoformat(etme)
            secs = int((e - s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except:
            pass

        # Проверяем, был ли предыдущий звонок с той же парой {caller, callee}
        pair_key = tuple(sorted([caller, callee])) if callee else None
        reply_id = hangup_reply_map.get(pair_key) if pair_key else None

        if is_internal:
            if cs == 2:
                m = f"✅ Успешный внутренний звонок\n{caller} ➡️ {callee}\n⌛ {dur} 🔈 Запись"
            else:
                m = f"❌ Абонент не ответил\n{caller} ➡️ {callee}\n⌛ {dur}"
        else:
            if ct == 1 and cs == 0:
                m = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone}"
                if dur: m += f"\n⌛ {dur}"
                for e in exts:
                    m += f" ☎️ {e}"
            elif ct == 0 and cs == 1:
                m = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone}"
                if dur: m += f"\n⌛ {dur}"
            elif ct == 0 and cs == 0:
                m = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone}"
                if dur: m += f"\n⌛ {dur}"
                for e in exts:
                    m += f" ☎️ {e}"
            elif ct == 0 and cs == 2:
                m = f"⬇️ ✅ Успешный входящий звонок\nАбонент: {phone}\n⌛ {dur} 🔈 Запись"
                if exts:
                    m += f" ☎️ {exts[0]}"
            elif ct == 1 and cs == 2:
                m = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {phone}\n⌛ {dur} 🔈 Запись"
                if exts:
                    m += f" ☎️ {exts[0]}"
            else:
                m = f"❌ Завершённый звонок\nАбонент: {phone}"
                if dur: m += f"\n⌛ {dur}"

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, m, reply_to_message_id=reply_id) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, m)
            if pair_key:
                hangup_reply_map[pair_key] = sent.message_id
        except:
            pass

        return {"status": "cleared"}

    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        await bot.send_message(TELEGRAM_CHAT_ID, txt)
    except:
        pass
    return {"status": "sent"}