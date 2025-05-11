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
hangup_reply_map = {}

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
        formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        # Ensure format like "+375 (29) 625-40-70"
        return formatted
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
    trunk = data.get("Trunk", "")

    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, raw={raw_phone}, data={data}")

    if et in {"init", "ping", "test", "healthcheck"}:
        return {"status": "ignored"}

    if et == "start":
        trunk_info = f"\nЛиния: {trunk}" if trunk else ""
        txt = f"🛎️ Входящий звонок\nАбонент: {phone}{trunk_info}"
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
            return {"status": "ignored"}

        # delete previous dial
        if uid in dial_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
            except:
                pass

        trunk_info = f"\nЛиния: {trunk}" if trunk else ""
        if not trunk and ct == 2:
            # internal
            txt = "🛎️ Внутренний звонок\n" + f"{exts[0]} ➡️ {phone}"
        else:
            if ct == 1:
                txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(exts)} ➡️ {phone}{trunk_info}"
            else:
                txt = f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ {', '.join(exts)}{trunk_info}"

        # delete start
        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except:
                pass

        sent = None
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": ct, "extensions": exts, "trunk": trunk}
            dial_phone_to_uid[raw_phone] = uid
        except:
            pass
        return {"status": "sent"}

    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))

        if caller in ("", "<unknown>") or connected in ("", "<unknown>"):
            return {"status": "ignored"}

        if re.fullmatch(r"\d{3}", caller):
            op, cli = caller, connected
        else:
            op, cli = connected, caller

        orig_uid = dial_phone_to_uid.get(cli)
        if not orig_uid:
            return {"status": "ignored"}

        key = (cli, op)
        if key in bridge_seen:
            return {"status": "ignored"}

        # delete dial message
        try:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        except:
            pass

        info = dial_cache.get(orig_uid, {})
        ct = info.get("call_type", 0)
        saved_trunk = info.get("trunk", "")
        tinfo = f"\nЛиния: {trunk or saved_trunk}" if (trunk or saved_trunk) else ""

        if status == 2:
            pre = "✅ Успешный исходящий звонок" if ct == 1 else "✅ Успешный входящий звонок"
        else:
            pre = "⏱ Идет внутренний разговор" if not trunk and ct == 2 else "⏱ Идет разговор"

        phone_cli = format_phone_number(cli)
        if not trunk and ct == 2:
            txt = f"{pre}\n{cli} ➡️ {op}"
        else:
            if ct == 0:
                txt = f"{pre}\nАбонент: {phone_cli} ➡️ {op}{tinfo}"
            else:
                txt = f"{pre}\n{op} ➡️ {phone_cli}{tinfo}"

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            bridge_store[orig_uid] = sent.message_id
            bridge_phone_index[cli] = orig_uid
            bridge_seen.add(key)
            active_bridges[orig_uid] = {"text": txt, "cli": cli, "op": op, "trunk": trunk}
        except:
            pass
        return {"status": "sent"}

    if et == "hangup":
        if not raw_phone:
            return {"status": "ignored"}

        # delete previous
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
        exts = [e for e in raw_exts if e]
        info = dial_cache.get(uid, {})
        if not exts:
            exts = info.get("extensions", [])
        if not trunk:
            trunk = info.get("trunk", "")
        tinfo = f"\nЛиния: {trunk}" if trunk else ""

        dur = ""
        try:
            s = datetime.fromisoformat(st)
            e = datetime.fromisoformat(etme)
            secs = int((e - s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except:
            pass

        prev = info.get("extensions", [])
        if prev == exts:
            if ct == 2 and not trunk:
                # internal hangup
                if cs == 2:
                    hangup_message = f"✅ Успешный внутренний звонок\n{prev[0]} ➡️ {exts[0]}\n⌛ {dur} 🔈 Запись"
                else:
                    hangup_message = f"❌ Абонент не ответил\n{prev[0]} ➡️ {exts[0]}\n⌛ {dur}"
            else:
                if ct == 1 and cs == 0:
                    hangup_message = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone}{tinfo}\n⌛ {dur}"
                elif ct == 0 and cs == 1:
                    hangup_message = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone}{tinfo}\n⌛ {dur}"
                elif ct == 0 and cs == 0:
                    hangup_message = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone}{tinfo}\n⌛ {dur}"
                elif ct == 0 and cs == 2:
                    hangup_message = f"⬇️ ✅ Успешный входящий звонок\nАбонент: {phone}{tinfo}\n⌛ {dur} 🔈 Запись"
                elif ct == 1 and cs == 2:
                    hangup_message = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {phone}{tinfo}\n⌛ {dur} 🔈 Запись"
                else:
                    hangup_message = f"❌ Завершённый звонок\nАбонент: {phone}{tinfo}\n⌛ {dur}"

            try:
                reply_id = hangup_reply_map.get(uid)
                sent = await bot.send_message(TELEGRAM_CHAT_ID, hangup_message, reply_to_message_id=reply_id) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, hangup_message)
                hangup_reply_map[uid] = sent.message_id
            except:
                pass

        return {"status": "cleared"}

    # fallback
    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        await bot.send_message(TELEGRAM_CHAT_ID, txt)
    except:
        pass
    return {"status": "sent"}
