from fastapi import FastAPI, Request
import logging
from telegram import Bot
import phonenumbers
import re
from db import init_db, log_event
import json
from datetime import datetime
import asyncio
import traceback

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

# Message stores
message_store = {}  # UniqueId -> message_id for start events
dial_store = {}     # UniqueId -> message_id for dial events
bridge_store = {}   # UniqueId -> message_id for bridge events

# Call tracking
bridge_seen = set()        # Set of caller-callee pairs in active bridges
dial_cache = {}            # UniqueId -> call details cache
dial_phone_to_uid = {}     # Phone number -> UniqueId mapping
active_bridges = {}        # UniqueId -> bridge details

# Caller-callee pair tracking
call_pair_message_map = {} # (caller, callee) -> latest message_id
hangup_message_map = {}    # caller -> latest hangup message_id

def format_phone_number(phone: str) -> str:
    logging.info(f"Original phone: {phone}")
    if not phone:
        return phone
    if is_internal_number(phone):
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
    return number and re.match(r"^\d{3,4}$", number)

def get_call_pair_key(caller, callee, is_internal=False):
    if not caller or caller == "<unknown>":
        return None
    if is_internal and callee:
        return tuple(sorted([caller, callee]))
    else:
        return (caller,)

def update_call_pair_message(caller, callee, message_id,禁止

System: You are Grok 3 built by xAI.

The user's message was cut off, but I can still address the issue based on the provided context and code. The problem is that the `hangup` event is only sent as a reply (comment) to a previous `start` event, but not after a `bridge` event, and the `start`, `dial`, and `bridge` events are not being sent as replies to the last `hangup` event associated with the same external phone number. Below, I’ll provide a solution to fix this by modifying the provided `main.py` code to ensure:
1. `start`, `dial`, and `bridge` events are sent as replies to the last `hangup` event for the same external phone number.
2. `hangup` events are consistently sent as replies to the last relevant message (including after `bridge`).
3. Preserve the existing functionality, including the 10-second `bridge` resend interval.

---

### Analysis of the Issue
Based on the user’s description:
1. **Why `start`, `dial`, `bridge` are not replies**:
   - The code attempts to fetch `reply_id` from `hangup_message_map` using `raw_phone` (for `start`, `dial`) or `orig_caller`/`orig_callee` (for `bridge`).
   - Possible issues:
     - `hangup_message_map` may not contain the external number’s `message_id` (e.g., cleared or not set).
     - The wrong number is used for lookup (e.g., `orig_caller` instead of `orig_callee` for outgoing calls in `bridge`).
     - The `is_valid_message_id` check may invalidate `reply_id` unnecessarily.

2. **Why `hangup` only replies after `start`**:
   - The `hangup` logic prioritizes `hangup_message_map[caller]`, then falls back to `bridge_store`, `dial_store`, or `message_store`.
   - After `bridge`, `orig_uid` is found in `bridge_store`, but if `reply_id` is invalid or missing, it doesn’t retry `hangup_message_map` for the external number.
   - Clearing `dial_phone_to_uid` in `hangup` may prevent subsequent events from finding `orig_uid`.

3. **Fixes Needed**:
   - Ensure `start`, `dial`, `bridge` use the correct external number for `hangup_message_map` lookup.
   - Prevent `dial_phone_to_uid` from being cleared in `hangup`.
   - Simplify or remove `is_valid_message_id` for `start`, `dial`, `bridge` to avoid discarding valid `reply_id`.
   - Enhance `hangup` to prioritize `hangup_message_map` for the external number and improve fallback logic.
   - Add detailed logging to diagnose issues.

---

### Updated Code
Below is the corrected `main.py`. Key changes:
- Removed `dial_phone_to_uid` cleanup in `hangup`.
- Adjusted `reply_id` lookup to consistently use the external number.
- Removed `is_valid_message_id` for `start`, `dial`, `bridge`.
- Improved `hangup` reply logic to prioritize external number lookup.
- Added more logging for debugging.

```python
from fastapi import FastAPI, Request
import logging
from telegram import Bot
import phonenumbers
import re
from db import init_db, log_event
import json
from datetime import datetime
import asyncio
import traceback

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

# Message stores
message_store = {}  # UniqueId -> message_id for start events
dial_store = {}     # UniqueId -> message_id for dial events
bridge_store = {}   # UniqueId -> message_id for bridge events

# Call tracking
bridge_seen = set()        # Set of caller-callee pairs in active bridges
dial_cache = {}            # UniqueId -> call details cache
dial_phone_to_uid = {}     # Phone number -> UniqueId mapping
active_bridges = {}        # UniqueId -> bridge details

# Caller-callee pair tracking
call_pair_message_map = {} # (caller, callee) -> latest message_id
hangup_message_map = {}    # caller -> latest hangup message_id

def format_phone_number(phone: str) -> str:
    logging.info(f"Original phone: {phone}")
    if not phone:
        return phone
    if is_internal_number(phone):
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
    return number and re.match(r"^\d{3,4}$", number)

def get_call_pair_key(caller, callee, is_internal=False):
    if not caller or caller == "<unknown>":
        return None
    if is_internal and callee:
        return tuple(sorted([caller, callee]))
    else:
        return (caller,)

def update_call_pair_message(caller, callee, message_id, is_internal=False):
    pair_key = get_call_pair_key(caller, callee, is_internal)
    if pair_key:
        call_pair_message_map[pair_key] = message_id
        if caller:
            call_pair_message_map[(caller,)] = message_id
        if callee:
            call_pair_message_map[(callee,)] = message_id
        logging.info(f"Updated call_pair_message_map: {pair_key} -> {message_id}")
        logging.info(f"Current call_pair_message_map state: {call_pair_message_map}")
    return pair_key

def get_call_pair_message(caller, callee, is_internal=False):
    pair_key = get_call_pair_key(caller, callee, is_internal)
    if pair_key and pair_key in call_pair_message_map:
        message_id = call_pair_message_map[pair_key]
        logging.info(f"Found message_id={message_id} for pair_key={pair_key}")
        return message_id
    if caller:
        for key, msg_id in call_pair_message_map.items():
            if caller in key:
                logging.info(f"Found message by partial match: caller={caller} in key={key}, msg_id={msg_id}")
                return msg_id
    logging.warning(f"No message found for caller={caller}, callee={callee}, is_internal={is_internal}")
    return None

async def is_valid_message_id(message_id: int) -> bool:
    try:
        await bot.get_chat_member(chat_id=TELEGRAM_CHAT_ID, user_id=bot.id)
        return True
    except Exception as e:
        logging.error(f"Invalid message_id={message_id}: {e}")
        return False

@app.on_event("startup")
async def start_bridge_resender():
    async def resend_loop():
        while True:
            await asyncio.sleep(10)  # 10-second interval
            for uid in list(active_bridges.keys()):
                msg_id = bridge_store.get(uid)
                if not msg_id:
                    continue
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, msg_id)
                except Exception as e:
                    logging.error(f"Failed to delete message in resend_loop: {e}")
                    pass
                try:
                    txt = active_bridges[uid]["text"]
                    caller = active_bridges[uid].get("cli")
                    callee = active_bridges[uid].get("op")
                    is_internal = is_internal_number(caller) and is_internal_number(callee)
                    reply_id = hangup_message_map.get(callee if not is_internal else caller)
                    logging.info(f"Bridge resend: Looking for reply_id for caller={caller}, callee={callee}, reply_id={reply_id}")
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=txt,
                        reply_to_message_id=reply_id
                    ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
                    bridge_store[uid] = sent.message_id
                    update_call_pair_message(caller, callee, sent.message_id, is_internal)
                    logging.info(f"Bridge resend: Sent message_id={sent.message_id} for UID={uid}, reply_id={reply_id}")
                except Exception as e:
                    logging.error(f"Failed to resend bridge message for UID={uid}: {e}")
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
    logging.info(f"Event {et}, UID={uid}, raw={raw_phone}, phone={phone}, data={data}")

    if et in {"init", "ping", "test", "healthcheck"}:
        return {"status": "ignored"}

    is_internal = call_type == 2 or (is_internal_number(raw_phone) and all(is_internal_number(e) for e in data.get("Extensions", [])))

    if et == "start":
        exts = data.get("Extensions", [])
        callee = exts[0] if exts else ""
        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone}"
        try:
            reply_id = hangup_message_map.get(raw_phone) if not is_internal else None
            logging.info(f"Start: Looking for reply_id for caller={raw_phone}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
            message_store[uid] = sent.message_id
            update_call_pair_message(raw_phone, callee, sent.message_id, is_internal)
            logging.info(f"Start: Saved message_id={sent.message_id} for caller={raw_phone}, callee={callee}, reply_id={reply_id}")
        except Exception as e:
            logging.error(f"Failed to send start message: {e}")
        return {"status": "sent"}

    if et == "dial":
        exts = data.get("Extensions", [])
        if not raw_phone or not exts:
            return {"status": "ignored"}
        callee = exts[0]
        if uid in dial_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
            except Exception as e:
                logging.error(f"Failed to delete dial message: {e}")
                pass
        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
        else:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(map(str, exts))} ➡️ {phone}" if call_type == 1 else f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ " + " ".join(f"🛎️{e}" for e in exts)
        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except Exception as e:
                logging.error(f"Failed to delete start message: {e}")
                pass
        try:
            reply_id = hangup_message_map.get(raw_phone) if not is_internal else None
            logging.info(f"Dial: Looking for reply_id for caller={raw_phone}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": call_type, "extensions": exts, "caller": raw_phone}
            dial_phone_to_uid[raw_phone] = uid
            if call_type == 1 and exts:  # For outgoing calls, map internal numbers
                for ext in exts:
                    dial_phone_to_uid[ext] = uid
            elif is_internal and callee:
                dial_phone_to_uid[callee] = uid
            update_call_pair_message(raw_phone, callee, sent.message_id, is_internal)
            logging.info(f"Dial: Saved message_id={sent.message_id} for caller={raw_phone}, callee={callee}, reply_id={reply_id}")
        except Exception as e:
            logging.error(f"Failed to send dial message: {e}")
        return {"status": "sent"}

    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))
        if caller == "<unknown>" and connected == "<unknown>":
            logging.info(f"Bridge ignored: both caller and connected are <unknown>")
            return {"status": "ignored"}
        orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(connected)
        if not orig_uid:
            logging.info(f"Bridge ignored: no orig_uid found for caller={caller}, connected={connected}")
            return {"status": "ignored"}
        dial_data = dial_cache.get(orig_uid, {})
        call_type = dial_data.get("call_type", call_type)
        if call_type == 1:  # Outgoing call
            orig_caller = connected  # Internal number
            orig_callee = dial_data.get("caller", caller)  # External number
        else:  # Incoming or internal call
            orig_caller = dial_data.get("caller", caller)
            orig_callee = connected
        key = tuple(sorted([orig_caller, orig_callee]))
        if key in bridge_seen:
            logging.info(f"Bridge ignored: key {key} already in bridge_seen")
            return {"status": "ignored"}
        if is_internal and caller != orig_caller:
            logging.info(f"Bridge ignored: internal call, caller {caller} != orig_caller {orig_caller}")
            return {"status": "ignored"}
        try:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        except Exception as e:
            logging.error(f"Failed to delete dial message in bridge: {e}")
            pass
        if is_internal:
            txt = f"⏱ Идет внутренний разговор\n{orig_caller} ➡️ {orig_callee}"
        else:
            pre = "✅ Успешный исходящий звонок" if call_type == 1 and status == 2 else "✅ Успешный входящий звонок" if call_type == 0 and status == 2 else "🛎️ Идет разговор"
            formatted_cli = format_phone_number(orig_caller)
            txt = f"{pre}\nАбонент: {orig_caller if is_internal_number(orig_caller) else formatted_cli} ➡️ 🛎️{orig_callee if is_internal_number(orig_callee) else format_phone_number(orig_callee)}"
        try:
            reply_id = hangup_message_map.get(orig_callee if call_type == 0 else orig_caller) if not is_internal else None
            logging.info(f"Bridge: Looking for reply_id for caller={orig_caller}, callee={orig_callee}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
            bridge_store[orig_uid] = sent.message_id
            bridge_seen.add(key)
            active_bridges[orig_uid] = {"text": txt, "cli": orig_caller, "op": orig_callee}
            update_call_pair_message(orig_caller, orig_callee, sent.message_id, is_internal)
            logging.info(f"Bridge: Saved message_id={sent.message_id} for caller={orig_caller}, callee={orig_callee}, reply_id={reply_id}")
        except Exception as e:
            logging.error(f"Failed to send bridge message: {e}")
        return {"status": "sent"}

    if et == "hangup":
        if not raw_phone:
            logging.info(f"Hangup: Ignored due to empty raw_phone, UID={uid}")
            return {"status": "ignored"}
        logging.info(f"Hangup: Processing for UID={uid}, raw_phone={raw_phone}, data={data}")
        for store in (message_store, dial_store, bridge_store):
            if uid in store:
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, store.pop(uid))
                except Exception as e:
                    logging.error(f"Hangup: Failed to delete message for UID={uid}: {e}")
        caller = raw_phone
        exts = [e for e in data.get("Extensions", []) if e and str(e).strip()]
        if not exts and uid in dial_cache:
            exts = dial_cache[uid].get("extensions", [])
        callee = exts[0] if exts else ""
        if not callee and uid in active_bridges:
            callee = active_bridges.get(uid, {}).get("op", "")
        if not callee and uid in dial_cache:
            callee = dial_cache[uid].get("extensions", [""])[0]
        active_bridges.pop(uid, None)
        if caller and callee:
            key = tuple(sorted([caller, callee]))
            bridge_seen.discard(key)
            # Removed dial_phone_to_uid cleanup to preserve mappings
        st = data.get("StartTime")
        etme = data.get("EndTime")
        cs = int(data.get("CallStatus", -1))
        ct = int(data.get("CallType", -1))
        dur = ""
        try:
            s = datetime.fromisoformat(st)
            e = datetime.fromisoformat(etme)
            secs = int((e - s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except Exception as e:
            logging.error(f"Hangup: Failed to calculate duration for UID={uid}: {e}")
        external_number = caller if ct == 0 else (callee if callee else caller)
        reply_id = hangup_message_map.get(external_number)
        logging.info(f"Hangup: Checked hangup_message_map for external_number={external_number}, reply_id={reply_id}")
        if reply_id:
            try:
                reply_id_int = int(reply_id)
                if not await is_valid_message_id(reply_id_int):
                    logging.warning(f"Hangup: reply_id={reply_id_int} is invalid, clearing from hangup_message_map")
                    reply_id = None
                    hangup_message_map.pop(external_number, None)
            except ValueError as ve:
                logging.error(f"Hangup: Invalid reply_id format: {reply_id}, error: {ve}")
                reply_id = None
                hangup_message_map.pop(external_number, None)
        if not reply_id:
            orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(callee)
            if orig_uid in bridge_store:
                reply_id = bridge_store[orig_uid]
                logging.info(f"Hangup: Found reply_id={reply_id} from bridge_store for UID={uid}")
            elif orig_uid in dial_store:
                reply_id = dial_store[orig_uid]
                logging.info(f"Hangup: Found reply_id={reply_id} from dial_store for UID={uid}")
            elif orig_uid in message_store:
                reply_id = message_store[orig_uid]
                logging.info(f"Hangup: Found reply_id={reply_id} from message_store for UID={uid}")
            if not reply_id:
                reply_id = get_call_pair_message(caller, callee, is_internal)
                logging.info(f"Hangup: Found reply_id={reply_id} from call_pair_message_map for UID={uid}")
        logging.info(f"Hangup: UID={uid}, caller={caller}, callee={callee}, reply_id={reply_id}, exts={exts}")
        logging.info(f"Hangup DEBUG: message_store={message_store}")
        logging.info(f"Hangup DEBUG: dial_store={dial_store}")
        logging.info(f"Hangup DEBUG: bridge_store={bridge_store}")
        logging.info(f"Hangup DEBUG: dial_phone_to_uid={dial_phone_to_uid}")
        logging.info(f"Hangup DEBUG: call_pair_message_map={call_pair_message_map}")
        logging.info(f"Hangup DEBUG: hangup_message_map={hangup_message_map}")
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
                for e in exts:
                    m += f" ☎️ {e}"
            elif ct == 1 and cs == 2:
                m = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {phone}\n⌛ {dur} 🔈 Запись"
                for e in exts:
                    m += f" ☎️ {e}"
            else:
                m = f"❌ Завершённый звонок\nАбонент: {phone}"
                if dur: m += f"\n⌛ {dur}"
        try:
            logging.info(f"Hangup: Preparing to send message for UID={uid}: text={m}, reply_id={reply_id}")
            sent = None
            if reply_id:
                try:
                    reply_id_int = int(reply_id)
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID, 
                        text=m, 
                        reply_to_message_id=reply_id_int
                    )
                    logging.info(f"Hangup: Successfully sent as reply with reply_id={reply_id_int} for UID={uid}, message_id={sent.message_id}")
                except ValueError as ve:
                    logging.error(f"Hangup: Invalid reply_id format: {reply_id}, error: {ve}")
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
                    logging.info(f"Hangup: Sent without reply_id due to invalid format for UID={uid}, message_id={sent.message_id}")
                except Exception as re:
                    logging.error(f"Hangup: Failed to send as reply: {re} for UID={uid}, reply_id={reply_id}")
                    logging.error(f"Hangup: Traceback: {traceback.format_exc()}")
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
                    logging.info(f"Hangup: Sent without reply_id after failed reply for UID={uid}, message_id={sent.message_id}")
            else:
                logging.info(f"Hangup: No reply_id found for UID={uid}, sending without reply")
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
                logging.info(f"Hangup: Sent without reply_id for UID={uid}, message_id={sent.message_id}")
            pair_key = update_call_pair_message(caller, callee, sent.message_id, is_internal)
            hangup_message_map[external_number] = sent.message_id
            logging.info(f"Hangup: Updated hangup_message_map: {external_number} -> {sent.message_id}")
            logging.info(f"Hangup: Sent message with id={sent.message_id} for pair_key={pair_key}")
        except Exception as e:
            logging.error(f"Hangup: Failed to send message: {e} for UID={uid}, text={m}")
            logging.error(f"Hangup: Traceback: {traceback.format_exc()}")
            try:
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
                pair_key = update_call_pair_message(caller, callee, sent.message_id, is_internal)
                hangup_message_map[external_number] = sent.message_id
                logging.info(f"Hangup: Retry succeeded with message_id={sent.message_id} for UID={uid}")
                logging.info(f"Hangup: Updated hangup_message_map: {external_number} -> {sent.message_id}")
            except Exception as e2:
                logging.error(f"Hangup: Retry also failed: {e2} for UID={uid}, text={m}")
                logging.error(f"Hangup: Retry traceback: {traceback.format_exc()}")
        return {"status": "sent"}

    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        await bot.send_message(TELEGRAM_CHAT_ID, txt)
    except Exception as e:
        logging.error(f"Failed to send event message: {e}")
    return {"status": "sent"}