# Строки 1-50
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
# Строки 1-50
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
# Строки 101-150
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

def save_telegram_message(message_id, event_type, token, caller, callee, is_internal, call_status=-1, call_type=-1, extensions=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        extensions_json = json.dumps(extensions if extensions is not None else [])
        cursor.execute('''
            INSERT INTO telegram_messages (message_id, event_type, token, caller, callee, is_internal, timestamp, call_status, call_type, extensions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (message_id, event_type, token, caller, callee, 1 if is_internal else 0, timestamp, call_status, call_type, extensions_json))
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
            SELECT message_id, token, caller, callee, is_internal, timestamp, call_status, call_type, extensions
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
            call_status = row['call_status']
            call_type = row['call_type']
            extensions = json.loads(row['extensions']) if row['extensions'] else []
            # Строки 151-200
            record = {
                'message_id': message_id,
                'caller': caller,
                'callee': callee,
                'timestamp': timestamp,
                'call_status': call_status,
                'call_type': call_type,
                'extensions': extensions
            }
            if row['is_internal']:
                if caller:
                    hangup_message_map[caller].append(record)
                if callee:
                    hangup_message_map[callee].append(record)
            else:
                if caller:
                    hangup_message_map[caller].append(record)
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
        if cc == "7":  # Специальная обработка для России (трехзначный код оператора)
            code = rest[:3] if len(rest) > 3 else rest
            num = rest[len(code):]
            return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
        else:
            code = rest[:2]
            num = rest[2:]
            return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except Exception:
        return phone
    # Строки 201-250
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

def update_hangup_message_map(caller, callee, message_id, is_internal=False, call_status=-1, call_type=-1, extensions=[]):
    external_number = caller if not is_internal else None
    if external_number:
        hangup_message_map[external_number].append({
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': datetime.now().isoformat(),
            'call_status': call_status,
            'call_type': call_type,
            'extensions': extensions
        })
        # Ограничиваем историю последними 5 записями
        hangup_message_map[external_number] = hangup_message_map[external_number][-5:]
        logging.info(f"Updated hangup_message_map: {external_number} -> {message_id}, history: {hangup_message_map[external_number]}")
    elif is_internal and caller and callee:
        hangup_message_map[caller].append({
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': datetime.now().isoformat(),
            'call_status': call_status,
            'call_type': call_type,
            'extensions': extensions
        })
        hangup_message_map[caller] = hangup_message_map[caller][-5:]
        hangup_message_map[callee].append({
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': datetime.now().isoformat(),
            'call_status': call_status,
            'call_type': call_type,
            'extensions': extensions
        })
        hangup_message_map[callee] = hangup_message_map[callee][-5:]
        logging.info(f"Updated hangup_message_map for internal call: {caller} and {callee}")
        # Строки 251-300
def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    def find_best_match(history, target_number):
        if not history:
            return None
        # Сортируем по времени (новейшие первыми)
        history = sorted(history, key=lambda x: x['timestamp'], reverse=True)
        # Возвращаем последнее сообщение для номера клиента
        return history[0]['message_id'] if history else None

    if not is_internal:
        # Для внешних вызовов ищем только по номеру клиента
        if caller and not is_internal_number(caller):
            history = hangup_message_map.get(caller, [])
            return find_best_match(history, caller)
        elif callee and not is_internal_number(callee):
            history = hangup_message_map.get(callee, [])
            return find_best_match(history, callee)
    # Для внутренних вызовов или если номер не найден, возвращаем None
    return None

def get_call_pair_message(caller, callee, is_internal=False):
    if not is_internal:
        # Для внешних вызовов ищем только по номеру клиента
        if caller and not is_internal_number(caller):
            for key, msg_id in call_pair_message_map.items():
                if caller in key:
                    logging.info(f"Found message by client number match: caller={caller} in key={key}, msg_id={msg_id}")
                    return msg_id
        elif callee and not is_internal_number(callee):
            for key, msg_id in call_pair_message_map.items():
                if callee in key:
                    logging.info(f"Found message by client number match: callee={callee} in key={key}, msg_id={msg_id}")
                    return msg_id
    logging.warning(f"No message found for caller={caller}, callee={callee}, is_internal={is_internal}")
    return None

def get_last_call_info(external_number: str) -> str:
    if not external_number or is_internal_number(external_number):
        return ""
    
    history = hangup_message_map.get(external_number, [])
    if not history:
        return ""
    
    # Считаем общее количество звонков напрямую из базы данных
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM telegram_messages
            WHERE event_type = 'hangup' AND caller = ?
        ''', (external_number,))
        call_count = cursor.fetchone()[0]
        conn.close()
    except Exception as e:
        logging.error(f"Failed to count hangups from database for {external_number}: {e}")
        call_count = len(history)  # В случае ошибки используем данные из памяти как fallback
        # Строки 301-350
    # Берем последний звонок (самый новый) из истории в памяти
    history = sorted(history, key=lambda x: x['timestamp'], reverse=True)
    last_call = history[0]
    last_timestamp = datetime.fromisoformat(last_call['timestamp'])
    # Добавляем поправку на GMT+3 (добавляем 3 часа)
    last_timestamp = last_timestamp.replace(hour=(last_timestamp.hour + 3) % 24)
    formatted_date = last_timestamp.strftime("%d.%m.%Y %H:%M")
    
    caller = last_call['caller']
    callee = last_call['callee']
    call_status = last_call['call_status']
    call_type = last_call['call_type']
    extensions = last_call['extensions']
    is_caller_external = not is_internal_number(caller)
    is_callee_external = not is_internal_number(callee)
    
    # Определяем направление звонка
    if is_caller_external and not is_callee_external:
        # Входящий звонок (клиент -> менеджер)
        direction = "incoming"
        manager = callee
        client = caller
    elif is_callee_external and not is_caller_external:
        # Исходящий звонок (менеджер -> клиент)
        direction = "outgoing"
        manager = caller
        client = callee
    else:
        return ""  # Возвращаем пустую строку, если направление не определено
    
    # Форматируем строку на основе направления и статуса
    status_text = ""
    if direction == "incoming":
        if call_status == 2:
            status_text = f"✅ 💰 ➡️ ☎️{manager}"
        elif call_status == 1:
            status_text = "❌ 💰🙅‍♂️"
        else:  # missed or no_answer (call_status == 0)
            if extensions and len(extensions) > 1:
                status_text = f"❌ 💰 ➡️ {' '.join([f'☎️{ext}' for ext in extensions])}"
            else:
                status_text = f"❌ 💰 ➡️ ☎️{manager}"
    else:  # outgoing
        if call_status == 2:
            status_text = f"✅ ☎️{manager} ➡️ 💰"
        else:  # no_answer or missed
            status_text = f"❌ ☎️{manager} ➡️ 💰"
    
    return f"🛎️ {call_count}\nПоследний: {formatted_date}\n{status_text}"
# Строки 301-350
    # Берем последний звонок (самый новый) из истории в памяти
    history = sorted(history, key=lambda x: x['timestamp'], reverse=True)
    last_call = history[0]
    last_timestamp = datetime.fromisoformat(last_call['timestamp'])
    # Добавляем поправку на GMT+3 (добавляем 3 часа)
    last_timestamp = last_timestamp.replace(hour=(last_timestamp.hour + 3) % 24)
    formatted_date = last_timestamp.strftime("%d.%m.%Y %H:%M")
    
    caller = last_call['caller']
    callee = last_call['callee']
    call_status = last_call['call_status']
    call_type = last_call['call_type']
    extensions = last_call['extensions']
    is_caller_external = not is_internal_number(caller)
    is_callee_external = not is_internal_number(callee)
    
    # Определяем направление звонка
    if is_caller_external and not is_callee_external:
        # Входящий звонок (клиент -> менеджер)
        direction = "incoming"
        manager = callee
        client = caller
    elif is_callee_external and not is_caller_external:
        # Исходящий звонок (менеджер -> клиент)
        direction = "outgoing"
        manager = caller
        client = callee
    else:
        return ""  # Возвращаем пустую строку, если направление не определено
    
    # Форматируем строку на основе направления и статуса
    status_text = ""
    if direction == "incoming":
        if call_status == 2:
            status_text = f"✅ 💰 ➡️ ☎️{manager}"
        elif call_status == 1:
            status_text = "❌ 💰🙅‍♂️"
        else:  # missed or no_answer (call_status == 0)
            if extensions and len(extensions) > 1:
                status_text = f"❌ 💰 ➡️ {' '.join([f'☎️{ext}' for ext in extensions])}"
            else:
                status_text = f"❌ 💰 ➡️ ☎️{manager}"
    else:  # outgoing
        if call_status == 2:
            status_text = f"✅ ☎️{manager} ➡️ 💰"
        else:  # no_answer or missed
            status_text = f"❌ ☎️{manager} ➡️ 💰"
    
    return f"🛎️ {call_count}\nПоследний: {formatted_date}\n{status_text}"
# Строки 388-430
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
            # Добавляем информацию о последнем звонке
            last_call_info = get_last_call_info(raw_phone)
            if last_call_info:
                txt += f"\n\n{last_call_info}"
        try:
            reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_internal) if not is_internal else None
            logging.info(f"Start: Looking for reply_id for caller={raw_phone}, reply_id={reply_id}")
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id,
                parse_mode='HTML'
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
            message_store[uid] = sent.message_id
            if not is_internal:
                update_call_pair_message(raw_phone, callee, sent.message_id, is_internal)
            save_telegram_message(sent.message_id, et, token, raw_phone, callee, is_internal)
            logging.info(f"Start: Saved message_id={sent.message_id} for caller={raw_phone}, callee={callee}, reply_id={reply_id}")
        except Exception as e:
            logging.error(f"Failed to send start message: {e}")
        return {"status": "sent"}
    # Строки 431-480
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
                # Исходящий звонок (внешний): форматируем номеродящий звонок (внешний): форматируем номер в столбик, убираем "Менеджер", используем ☎️
                txt = f"⬆️ <b>Набираем номер</b>\n☎️ {', '.join(map(str, exts))} ➡️\n💰 {display_phone}"
            else:
                # Входящий звонок (внешний): форматируем экстеншены в столбик после стрелки, убираем "Абонент"
                txt = f"🛎️ <b>Входящий разговор</b>\n💰 {display_phone} ➡️\n" + "\n".join(f"☎️ {e}" for e in exts)
            # Добавляем информацию о последнем звонке
            last_call_info = get_last_call_info(raw_phone if call_type != 1 else callee)
            if last_call_info:
                txt += f"\n\n{last_call_info}"
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
                reply_to_message_id=reply_id,
                parse_mode='HTML'
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
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
    # Строки 471-510
    # Строки для замены в событии bridge
    if et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))
        logging.info(f"Bridge: Processing with caller={caller}, connected={connected}, status={status}, full_data={data}")
        # Временно убираем игнорирование для отладки
        if caller == "<unknown>" and connected == "<unknown>":
            logging.warning(f"Bridge: both caller and connected are <unknown>, continuing for debug")
        orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(connected)
        if not orig_uid:
            logging.warning(f"Bridge: no orig_uid found for caller={caller}, connected={connected}, dial_phone_to_uid={dial_phone_to_uid}")
            # Временно продолжаем обработку даже без orig_uid для отладки
            orig_uid = uid  # Используем текущий UID как fallback
            dial_data = {}
            call_type = 0  # Default to incoming
        else:
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
            logging.info(f"Bridge: key {key} already in bridge_seen, continuing for debug")
        if is_internal and caller != orig_caller:
            logging.info(f"Bridge: internal call mismatch, caller {caller} != orig_caller {orig_caller}, continuing for debug")
        try:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        except Exception as e:
            logging.error(f"Failed to delete dial message in bridge: {e}")
            pass
        if is_internal:
            # Проверяем, совпадают ли номера, и добавляем альтернативный текст с данными из Channel или Exten
            if orig_caller == orig_callee:
                channel_info = data.get("Channel", "Unknown Channel")
                exten_info = data.get("Exten", "Unknown Exten")
                txt = f"⏱ Идет внутренний разговор (возможно ошибка данных)\n{orig_caller} ➡️ {orig_callee} (same number)\n📡 Channel: {channel_info}\n🔄 Exten: {exten_info}"
            else:
                txt = f"⏱ Идет внутренний разговор\n{orig_caller} ➡️ {orig_callee}"
        else:
            if call_type == 1 and status == 2:
                pre = "✅ Успешный исходящий звонок"
            elif call_type == 1:
                pre = "⬆️ 💬 <b>Исходящий разговор</b>"
            elif call_type == 0 and status == 2:
                pre = "✅ Успешный входящий звонок"
            else:
                pre = "⬇️ 💬 <b>Входящий разговор</b>"
            formatted_cli = format_phone_number(orig_caller)
            # Проверяем, начинается ли номер с +000
            display_callee = orig_callee if is_internal_number(orig_callee) else format_phone_number(orig_callee)
            if not is_internal_number(orig_callee) and display_callee.startswith("+000"):
                display_callee = "Номер не определен"
            manager_emoji = "☎️" if is_internal_number(orig_caller) else "💰"
            callee_emoji = "☎️" if is_internal_number(orig_callee) else "💰"
            if orig_caller == orig_callee:
                channel_info = data.get("Channel", "Unknown Channel")
                exten_info = data.get("Exten", "Unknown Exten")
                txt = f"{pre} (возможно ошибка данных)\n{manager_emoji} {orig_caller if is_internal_number(orig_caller) else formatted_cli} ➡️ {callee_emoji} {display_callee} (same number)\n📡 Channel: {channel_info}\n🔄 Exten: {exten_info}"
            else:
                txt = f"{pre}\n{manager_emoji} {orig_caller if is_internal_number(orig_caller) else formatted_cli} ➡️ {callee_emoji} {display_callee}"
            # Добавляем информацию о последнем звонке
            external_num = orig_callee if call_type == 1 else orig_caller
            last_call_info = get_last_call_info(external_num)
            if last_call_info:
                txt += f"\n\n{last_call_info}"
        try:
            reply_id = get_relevant_hangup_message_id(orig_caller, orig_callee, is_internal) if not is_internal else None
            logging.info(f"Bridge: Looking for reply_id for caller={orig_caller}, callee={orig_callee}, reply_id={reply_id}")
            sent = None
            for attempt in range(2):  # Делаем 2 попытки отправки
                try:
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=txt,
                        reply_to_message_id=reply_id,
                        parse_mode='HTML'
                    ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
                    logging.info(f"Bridge: Successfully sent message on attempt {attempt+1}, message_id={sent.message_id}")
                    break
                except Exception as send_error:
                    logging.error(f"Bridge: Send attempt {attempt+1} failed: {send_error}")
                    if attempt == 1:
                        logging.error(f"Bridge: All send attempts failed for UID={uid}")
            if sent:
                bridge_store[orig_uid] = sent.message_id
                bridge_seen.add(key)
                active_bridges[orig_uid] = {"text": txt, "cli": orig_caller, "op": orig_callee}
                if not is_internal:
                    update_call_pair_message(orig_caller, orig_callee, sent.message_id, is_internal)
                save_telegram_message(sent.message_id, et, token, orig_caller, orig_callee, is_internal)
                logging.info(f"Bridge: Saved message_id={sent.message_id} for caller={orig_caller}, callee={orig_callee}, reply_id={reply_id}")
        except Exception as e:
            logging.error(f"Bridge: Failed to send message after retries: {e}")
        return {"status": "sent"}
    # Строки 551-600
    if et == "hangup":
        if not raw_phone:
            logging.warning(f"Hangup: Ignored due to empty raw_phone, UID={uid}, full_data={data}")
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
                # Строки 551-600
    if et == "hangup":
        if not raw_phone:
            logging.warning(f"Hangup: Ignored due to empty raw_phone, UID={uid}, full_data={data}")
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
                # Строки 651-700
        try:
            logging.info(f"Hangup: Preparing to send message for UID={uid}: text={m}, reply_id={reply_id}")
            sent = None
            if reply_id:
                try:
                    reply_id_int = int(reply_id)
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID, 
                        text=m, 
                        reply_to_message_id=reply_id_int,
                        parse_mode='HTML'
                    )
                    logging.info(f"Hangup: Successfully sent as reply with reply_id={reply_id_int} for UID={uid}, message_id={sent.message_id}")
                except ValueError as ve:
                    logging.error(f"Hangup: Invalid reply_id format: {reply_id}, error: {ve}")
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
                    logging.info(f"Hangup: Sent without reply_id due to invalid format for UID={uid}, message_id={sent.message_id}")
                except Exception as re:
                    logging.error(f"Hangup: Failed to send as reply: {re} for UID={uid}, reply_id={reply_id}")
                    logging.error(f"Hangup: Traceback: {traceback.format_exc()}")
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
                    logging.info(f"Hangup: Sent without reply_id after failed reply for UID={uid}, message_id={sent.message_id}")
            else:
                logging.info(f"Hangup: No reply_id found for UID={uid}, sending without reply")
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
                logging.info(f"Hangup: Sent without reply_id for UID={uid}, message_id={sent.message_id}")
            if not is_internal:
                pair_key = update_call_pair_message(caller, callee, sent.message_id, is_internal)
                update_hangup_message_map(caller, callee, sent.message_id, is_internal, cs, ct, exts)
                logging.info(f"Hangup: Sent message with id={sent.message_id} for pair_key={pair_key}")
            save_telegram_message(sent.message_id, et, token, caller, callee, is_internal, cs, ct, exts)
        except Exception as e:
            logging.error(f"Hangup: Failed to send message: {e} for UID={uid}, text={m}")
            logging.error(f"Hangup: Traceback: {traceback.format_exc()}")
            try:
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
                if not is_internal:
                    pair_key = update_call_pair_message(caller, callee, sent.message_id, is_internal)
                    update_hangup_message_map(caller, callee, sent.message_id, is_internal, cs, ct, exts)
                    logging.info(f"Hangup: Retry succeeded with message_id={sent.message_id} for UID={uid}")
                save_telegram_message(sent.message_id, et, token, caller, callee, is_internal, cs, ct, exts)
            except Exception as e2:
                logging.error(f"Hangup: Retry also failed: {e2} for UID={uid}, text={m}")
                logging.error(f"Hangup: Retry traceback: {traceback.format_exc()}")
        return {"status": "sent"}
    # Строки 701-722
    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        reply_id = get_relevant_hangup_message_id(raw_phone, "", is_internal) if not is_internal else None
        sent = await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=txt,
            reply_to_message_id=reply_id,
            parse_mode='HTML'
        ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
        save_telegram_message(sent.message_id, et, token, raw_phone, "", is_internal)
        logging.info(f"Sent generic event message with reply_id={reply_id}")
    except Exception as e:
        logging.error(f"Failed to send event message: {e}")
    return {"status": "sent"}