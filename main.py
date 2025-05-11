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
active_bridges = {}  # UID: {"text": str, "cli": str, "op": str}
hangup_reply_map = {}  # phone → last message_id


def format_phone_number(phone: str) -> str:
    logging.info(f"Original phone: {phone}")
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

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, raw={raw_phone}, data={data}")

    if et in {"init", "ping", "test", "healthcheck"}:
        logging.info(f"Ignored event type: {et}")
        return {"status": "ignored"}

    if et == "start":
        txt = f"🛎️ Входящий звонок\nАбонент: {phone}"
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            message_store[uid] = sent.message_id
        except:
            pass
        return {"status": "sent"}

    if et == "dial":
        ct = int(data.get("CallType", 0))
        exts = data.get("Extensions", [])
        if not raw_phone:
            logging.info(f"Ignored dial without phone. UID={uid}")
            return {"status": "ignored"}
        if ct == 1:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(map(str, exts))} ➡️ {phone}"
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ " + " ".join(f"🛎️{e}" for e in exts)
        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except:
                pass
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": ct, "extensions": exts}
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

        if re.fullmatch(r"\d{3}", caller):
            op, cli = caller, connected
        else:
            op, cli = connected, caller

        if not cli:
            return {"status": "ignored"}

        orig_uid = dial_phone_to_uid.get(cli)
        if not orig_uid:
            return {"status": "ignored"}

        key = (cli, op)
        if key in bridge_seen:
            return {"status": "ignored"}

        try:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        except:
            pass

        ct = dial_cache.get(orig_uid, {}).get("call_type", 0)
        if ct == 1 and status == 2:
            pre = "✅ Успешный исходящий звонок"
        elif ct == 0 and status == 2:
            pre = "✅ Успешный входящий звонок"
        else:
            pre = "🛎️ Идет разговор"

        formatted_cli = format_phone_number(cli)
        txt = f"{pre}\nАбонент: {formatted_cli} ➡️ 🛎️{op}"
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
            logging.info(f"Ignored hangup without phone. UID={uid}")
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

        dur = ""
        try:
            s = datetime.fromisoformat(st)
            e = datetime.fromisoformat(etme)
            secs = int((e - s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except:
            pass

        if ct == 1 and cs == 0:
            m = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone}"
            if dur:
                m += f"\n⌛ {dur}"
            for e in exts:
                m += f" ☎️ {e}"
        elif ct == 0 and cs == 1:
            m = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone}"
            if dur:
                m += f"\n⌛ {dur}"
        elif ct == 0 and cs == 0:
            m = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone}"
            if dur:
                m += f"\n⌛ {dur}"
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
            if dur:
                m += f"\n⌛ {dur}"

        try:
            reply_id = hangup_reply_map.get(phone)
            sent = await bot.send_message(TELEGRAM_CHAT_ID, m, reply_to_message_id=reply_id) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, m)
            hangup_reply_map[phone] = sent.message_id
        except:
            pass

        return {"status": "cleared"}

    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        await bot.send_message(TELEGRAM_CHAT_ID, txt)
    except:
        pass
    return {"status": "sent"}
