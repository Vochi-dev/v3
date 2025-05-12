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
from collections import defaultdict
import sqlite3

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
hangup_message_map = defaultdict(list)  # number -> list of hangup records (message_id, caller, callee, timestamp)

# Database connection for storing events and Telegram message history
def get_db_connection():
    try:
        conn = sqlite3.connect("/root/asterisk-webhook/asterisk_events.db")
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        raise

def init_database_tables():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Проверяем, есть ли поле token в таблице events
        cursor.execute("PRAGMA table_info(events)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'token' not in columns:
            try:
                cursor.execute("ALTER TABLE events ADD COLUMN token TEXT")
                logging.info("Added 'token' column to 'events' table")
            except Exception as e:
                logging.warning(f"Could not add 'token' column to 'events' table: {e}")
        
        # Таблица для сообщений Telegram с токеном
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                token TEXT,
                caller TEXT NOT NULL,
                callee TEXT,
                is_internal INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("Initialized database tables: checked 'events' and created 'telegram_messages' if needed")
    except Exception as e:
        logging.error(f"Failed to initialize database tables: {e}")

def save_asterisk_event(event_type, unique_id, token, event_data):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        raw_json = json.dumps(event_data)
        cursor.execute('''
            INSERT INTO events (timestamp, event_type, unique_id, raw_json, token)
            VALUES (?, ?, ?, ?, ?)
        ''', (timestamp, event_type, unique_id, raw_json, token))
        conn.commit()
        conn.close()
        logging.info(f"Saved Asterisk event: type={event_type}, unique_id={unique_id}, token={token}")
    except Exception as e:
        logging.error(f"Failed to save Asterisk event: {e}")

def save_telegram_message(message_id, event_type, token, caller, callee, is_internal):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO telegram_messages (message_id, event_type, token, caller, callee, is_internal, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (message_id, event_type, token, caller, callee, 1 if is_internal else 0, timestamp))
        conn.commit()
        conn.close()
        logging.info(f"Saved Telegram message: message_id={message_id}, event_type={event_type}, token={token}, caller={caller}, callee={callee}")
    except Exception as e:
        logging.error(f"Failed to save Telegram message: {e}")

def load_hangup_message_history():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Загружаем последние 100 записей типа hangup для восстановления истории
        cursor.execute('''
            SELECT message_id, token, caller, callee, is_internal, timestamp
            FROM telegram_messages
            WHERE event_type = 'hangup'
            ORDER BY timestamp DESC
            LIMIT 100
        ''')
        rows = cursor.fetchall()
        hangup_message_map.clear()
        for row in rows:
            caller = row['caller']
            callee = row['callee']
            message_id = row['message_id']
            timestamp = row['timestamp']
            if row['is_internal']:
                if caller:
                    hangup_message_map[caller].append({
                        'message_id': message_id,
                        'caller': caller,
                        'callee': callee,
                        'timestamp': timestamp
                    })
                if callee:
                    hangup_message_map[callee].append({
                        'message_id': message_id,
                        'caller': caller,
                        'callee': callee,
                        'timestamp': timestamp
                    })
            else:
                if caller:
                    hangup_message_map[caller].append({
                        'message_id': message_id,
                        'caller': caller,
                        'callee': callee,
                        'timestamp': timestamp
                    })
        # Ограничиваем историю последними 5 записями для каждого номера
        for key in hangup_message_map:
            hangup_message_map[key] = hangup_message_map[key][-5:]
        conn.close()
        logging.info(f"Loaded hangup message history: {hangup_message_map}")
    except Exception as e:
        logging.error(f"Failed to load hangup message history: {e}")

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

def update_hangup_message_map(caller, callee, message_id, is_internal=False):
    external_number = caller if not is_internal else None
    if external_number:
        hangup_message_map[external_number].append({
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': datetime.now().isoformat()
        })
        # Ограничиваем историю последними 5 записями
        hangup_message_map[external_number] = hangup_message_map[external_number][-5:]
        logging.info(f"Updated hangup_message_map: {external_number} -> {message_id}, history: {hangup_message_map[external_number]}")
    elif is_internal and caller and callee:
        hangup_message_map[caller].append({
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': datetime.now().isoformat()
        })
        hangup_message_map[caller] = hangup_message_map[caller][-5:]
        hangup_message_map[callee].append({
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': datetime.now().isoformat()
        })
        hangup_message_map[callee] = hangup_message_map[callee][-5:]
        logging.info(f"Updated hangup_message_map for internal call: {caller} and {callee}")

def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    def find_best_match(history, target_caller, target_callee):
        if not history:
            return None
        # Сортируем по времени (новейшие первыми)
        history = sorted(history, key=lambda x: x['timestamp'], reverse=True)
        # Сначала ищем точное совпадение пары caller-callee
        for entry in history:
            if entry['caller'] == target_caller and entry['callee'] == target_callee:
                return entry['message_id']
        # Если точного совпадения нет, возвращаем последнее сообщение для caller
        return history[0]['message_id'] if history else None

    if not is_internal and caller:
        history = hangup_message_map.get(caller, [])
        return find_best_match(history, caller, callee)
    elif not is_internal and callee:
        history = hangup_message_map.get(callee, [])
        return find_best_match(history, caller, callee)
    elif is_internal and caller and callee:
        history_caller = hangup_message_map.get(caller, [])
        history_callee = hangup_message_map.get(callee, [])
        reply_id_caller = find_best_match(history_caller, caller, callee)
        reply_id_callee = find_best_match(history_callee, caller, callee)
        return reply_id_caller or reply_id_callee
    return None

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
async def startup_tasks():
    logging.info("Starting up the application...")
    try:
        # Инициализация таблиц в базе данных
        init_database_tables()
        # Загрузка истории hangup-сообщений из базы данных
        load_hangup_message_history()
    except Exception as e:
        logging.error(f"Failed during startup tasks: {e}")
        logging.error(f"Startup traceback: {traceback.format_exc()}")

    # Запуск цикла переотправки bridge-сообщений
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
                    reply_id = get_relevant_hangup_message_id(caller, callee, is_internal) if not is_internal else None
                    token = dial_cache.get(uid, {}).get("token", "")
                    logging.info(f"Bridge resend: Looking for reply_id for caller={caller}, callee={callee}, reply_id={reply_id}")
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=txt,
                        reply_to_message_id=reply_id
                    ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
                    bridge_store[uid] = sent.message_id
                    if not is_internal:
                        update_call_pair_message(caller, callee, sent.message_id, is_internal)
                    save_telegram_message(sent.message_id, "bridge_resend", token, caller, callee, is_internal)
                    logging.info(f"Bridge resend: Sent message_id={sent.message_id} for UID={uid}, reply_id={reply_id}")
                except Exception as e:
                    logging.error(f"Failed to resend bridge message for UID={uid}: {e}")
                    pass
    asyncio.create_task(resend_loop())
    logging.info("Startup tasks completed, resend loop started")

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
    phone = format_phone_number(raw_phone)
    call_type = int(data.get("CallType", 0))
    # Извлекаем токен из данных события (используем поле Token из лога)
    token = data.get("Token", "")  # Теперь используем поле Token как токен

    # Сохраняем событие Asterisk в базу данных
    save_asterisk_event(et, uid, token, data)
    log_event(et, uid, json.dumps(data))
    logging.info(f"Event {et}, UID={uid}, raw={raw_phone}, phone={phone}, token={token}, data={data}")

    if et in {"init", "ping", "test", "healthcheck"}:
        return {"status": "ignored"}

    is_internal = call_type == 2 or (is_internal_number(raw_phone) and all(is_internal_number(e) for e in data.get("Extensions", [])))

    if et == "start":
        exts = data.get("Extensions", [])
        callee = exts[0] if exts else ""
        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
        else:
            # Проверяем, начинается ли номер с +000
            display_phone = phone if not phone.startswith("+000") else "Номер не определен"
            txt = f"🛎️ Входящий звонок\n💰 {display_phone}"
        try:
            reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_internal) if not is_internal else None
            logging.info(f"Start: Looking for reply_id for caller={raw_phone}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
            message_store[uid] = sent.message_id
            if not is_internal:
                update_call_pair_message(raw_phone, callee, sent.message_id, is_internal)
            save_telegram_message(sent.message_id, et, token, raw_phone, callee, is_internal)
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
            # Для внутренних звонков форматирование без переносов строк
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
        else:
            # Проверяем, начинается ли номер с +000
            display_phone = phone if not phone.startswith("+000") else "Номер не определен"
            if call_type == 1:
                # Исходящий звонок (внешний): форматируем номер в столбик, убираем "Менеджер", используем ☎️
                txt = f"🛎️ Исходящий звонок\n☎️ {', '.join(map(str, exts))} ➡️\n💰 {display_phone}"
            else:
                # Входящий звонок (внешний): форматируем экстеншены в столбик после стрелки, убираем "Абонент"
                txt = f"🛎️ Входящий звонок\n💰 {display_phone} ➡️\n" + "\n".join(f"☎️ {e}" for e in exts)
        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except Exception as e:
                logging.error(f"Failed to delete start message: {e}")
                pass
        try:
            reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_internal) if not is_internal else None
            logging.info(f"Dial: Looking for reply_id for caller={raw_phone}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": call_type, "extensions": exts, "caller": raw_phone, "token": token}
            dial_phone_to_uid[raw_phone] = uid
            if call_type == 1 and exts:  # For outgoing calls, map internal numbers
                for ext in exts:
                    dial_phone_to_uid[ext] = uid
            elif is_internal and callee:
                dial_phone_to_uid[callee] = uid
            if not is_internal:
                update_call_pair_message(raw_phone, callee, sent.message_id, is_internal)
            save_telegram_message(sent.message_id, et, token, raw_phone, callee, is_internal)
            logging.info(f"Dial: Saved message_id={sent.message_id} for caller={raw_phone}, callee={callee}, reply_id={reply_id}")
        except Exception as e:
            logging.error(f"Failed to send dial message: {e}")
        return {"status": "sent"}

    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))
        logging.info(f"Bridge: Processing with caller={caller}, connected={connected}, status={status}")
        if caller == "<unknown>" and connected == "<unknown>":
            logging.info(f"Bridge ignored: both caller and connected are <unknown>")
            return {"status": "ignored"}
        orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(connected)
        if not orig_uid:
            logging.info(f"Bridge ignored: no orig_uid found for caller={caller}, connected={connected}, dial_phone_to_uid={dial_phone_to_uid}")
            return {"status": "ignored"}
        dial_data = dial_cache.get(orig_uid, {})
        call_type = dial_data.get("call_type", call_type)
        token = dial_data.get("token", token)  # Используем токен из кэша или текущий
        logging.info(f"Bridge: orig_uid={orig_uid}, call_type={call_type}, dial_data={dial_data}")
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
            if call_type == 1 and status == 2:
                pre = "✅ Успешный исходящий звонок"
            elif call_type == 1:
                pre = "⬆️ 💬 Исходящий разговор"
            elif call_type == 0 and status == 2:
                pre = "✅ Успешный входящий звонок"
            else:
                pre = "⬇️ 💬 Входящий разговор"
            formatted_cli = format_phone_number(orig_caller)
            # Проверяем, начинается ли номер с +000
            display_callee = orig_callee if is_internal_number(orig_callee) else format_phone_number(orig_callee)
            if not is_internal_number(orig_callee) and display_callee.startswith("+000"):
                display_callee = "Номер не определен"
            # Для внешних звонков используем ☎️ перед номером менеджера
            manager_emoji = "☎️" if is_internal_number(orig_caller) else "💰"
            callee_emoji = "☎️" if is_internal_number(orig_callee) else "💰"
            txt = f"{pre}\n{manager_emoji} {orig_caller if is_internal_number(orig_caller) else formatted_cli} ➡️ {callee_emoji} {display_callee}"
        try:
            reply_id = get_relevant_hangup_message_id(orig_caller, orig_callee, is_internal) if not is_internal else None
            logging.info(f"Bridge: Looking for reply_id for caller={orig_caller}, callee={orig_callee}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
            bridge_store[orig_uid] = sent.message_id
            bridge_seen.add(key)
            active_bridges[orig_uid] = {"text": txt, "cli": orig_caller, "op": orig_callee}
            if not is_internal:
                update_call_pair_message(orig_caller, orig_callee, sent.message_id, is_internal)
            save_telegram_message(sent.message_id, et, token, orig_caller, orig_callee, is_internal)
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
        token = dial_cache.get(uid, {}).get("token", token)  # Используем токен из кэша или текущий
        active_bridges.pop(uid, None)
        if caller and callee:
            key = tuple(sorted([caller, callee]))
            bridge_seen.discard(key)
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
        reply_id = get_relevant_hangup_message_id(caller, callee, is_internal) if not is_internal else None
        logging.info(f"Hangup: Checked hangup_message_map for external_number={external_number}, caller={caller}, callee={callee}, reply_id={reply_id}")
        if not reply_id and not is_internal:
            orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(callee)
            logging.info(f"Hangup: orig_uid={orig_uid}, dial_phone_to_uid={dial_phone_to_uid}")
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
        logging.info(f"Hangup: UID={uid}, caller={caller}, callee={callee}, reply_id={reply_id}, exts={exts}, call_type={ct}")
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
            # Проверяем, начинается ли номер с +000
            display_phone = phone if not phone.startswith("+000") else "Номер не определен"
            if ct == 1 and cs == 0:
                m = f"⬆️ ❌ Абонент не ответил\n💰 {display_phone}"
                if dur: m += f"\n⌛ {dur}"
                for e in exts:
                    m += f" ☎️ {e}"
            elif ct == 0 and cs == 1:
                m = f"⬇️ ❌ Абонент положил трубку\n💰 {display_phone}"
                if dur: m += f"\n⌛ {dur}"
            elif ct == 0 and cs == 0:
                m = f"⬇️ ❌ Неотвеченный звонок\n💰 {display_phone}"
                if dur: m += f"\n⌛ {dur}"
                for e in exts:
                    m += f" ☎️ {e}"
            elif ct == 0 and cs == 2:
                m = f"⬇️ ✅ Успешный входящий звонок\n💰 {display_phone}\n⌛ {dur} 🔈 Запись"
                for e in exts:
                    m += f" ☎️ {e}"
            elif ct == 1 and cs == 2:
                m = f"⬆️ ✅ Успешный исходящий звонок\n💰 {display_phone}\n⌛ {dur} 🔈 Запись"
                for e in exts:
                    m += f" ☎️ {e}"
            else:
                m = f"❌ Завершённый звонок\n💰 {display_phone}"
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
            if not is_internal:
                pair_key = update_call_pair_message(caller, callee, sent.message_id, is_internal)
                update_hangup_message_map(caller, callee, sent.message_id, is_internal)
                logging.info(f"Hangup: Sent message with id={sent.message_id} for pair_key={pair_key}")
            save_telegram_message(sent.message_id, et, token, caller, callee, is_internal)
        except Exception as e:
            logging.error(f"Hangup: Failed to send message: {e} for UID={uid}, text={m}")
            logging.error(f"Hangup: Traceback: {traceback.format_exc()}")
            try:
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m)
                if not is_internal:
                    pair_key = update_call_pair_message(caller, callee, sent.message_id, is_internal)
                    update_hangup_message_map(caller, callee, sent.message_id, is_internal)
                    logging.info(f"Hangup: Retry succeeded with message_id={sent.message_id} for UID={uid}")
                save_telegram_message(sent.message_id, et, token, caller, callee, is_internal)
            except Exception as e2:
                logging.error(f"Hangup: Retry also failed: {e2} for UID={uid}, text={m}")
                logging.error(f"Hangup: Retry traceback: {traceback.format_exc()}")
        return {"status": "sent"}

    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        reply_id = get_relevant_hangup_message_id(raw_phone, "", is_internal) if not is_internal else None
        sent = await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=txt,
            reply_to_message_id=reply_id
        ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt)
        save_telegram_message(sent.message_id, et, token, raw_phone, "", is_internal)
        logging.info(f"Sent generic event message with reply_id={reply_id}")
    except Exception as e:
        logging.error(f"Failed to send event message: {e}")
    return {"status": "sent"}