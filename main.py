from fastapi import FastAPI, Request
import logging
from telegram import Bot
import phonenumbers
import re
import json
from datetime import datetime
import asyncio
import traceback
from collections import defaultdict
import sqlite3

app = FastAPI()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("/root/asterisk-webhook/asterisk_events.db")
    cursor = conn.cursor()
    
    # Таблица событий Asterisk
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            unique_id TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            token TEXT
        )
    ''')
    
    # Таблица сообщений Telegram
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            token TEXT,
            caller TEXT NOT NULL,
            callee TEXT,
            is_internal INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            call_status INTEGER DEFAULT -1,
            call_type INTEGER DEFAULT -1,
            extensions TEXT DEFAULT '[]'
        )
    ''')
    
    # Таблица истории звонков
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS call_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller TEXT NOT NULL,
            callee TEXT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration INTEGER,
            call_status INTEGER NOT NULL,
            call_type INTEGER NOT NULL,
            is_internal INTEGER NOT NULL,
            token TEXT,
            UNIQUE(caller, callee, start_time)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Настройка логгирования
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Конфигурация Telegram бота
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Хранилища сообщений
message_store = {}
dial_store = {}
bridge_store = {}

# Трекинг звонков
bridge_seen = set()
dial_cache = {}
dial_phone_to_uid = {}
active_bridges = {}

# Маппинги для истории звонков
call_pair_message_map = {}
hangup_message_map = defaultdict(list)

def get_db_connection():
    try:
        conn = sqlite3.connect("/root/asterisk-webhook/asterisk_events.db")
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        raise

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
    except Exception as e:
        logging.error(f"Failed to save Telegram message: {e}")

def save_call_history(caller, callee, start_time, end_time, call_status, call_type, is_internal, token):
    try:
        duration = None
        if start_time and end_time:
            try:
                start = datetime.fromisoformat(start_time)
                end = datetime.fromisoformat(end_time)
                duration = int((end - start).total_seconds())
            except:
                pass
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO call_history 
            (caller, callee, start_time, end_time, duration, call_status, call_type, is_internal, token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (caller, callee, start_time, end_time, duration, call_status, call_type, 1 if is_internal else 0, token))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to save call history: {e}")

def load_call_history():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Загружаем историю звонков
        cursor.execute('''
            SELECT caller, callee, start_time, end_time, duration, call_status, call_type, is_internal
            FROM call_history
            ORDER BY start_time DESC
            LIMIT 1000
        ''')
        rows = cursor.fetchall()
        
        # Загружаем hangup сообщения
        cursor.execute('''
            SELECT caller, callee, timestamp, call_status, call_type, is_internal
            FROM telegram_messages
            WHERE event_type = 'hangup'
            ORDER BY timestamp DESC
            LIMIT 1000
        ''')
        hangup_rows = cursor.fetchall()
        
        conn.close()
        
        # Восстанавливаем hangup_message_map
        hangup_message_map.clear()
        for row in hangup_rows:
            caller = row['caller']
            callee = row['callee']
            record = {
                'caller': caller,
                'callee': callee,
                'timestamp': row['timestamp'],
                'call_status': row['call_status'],
                'call_type': row['call_type'],
                'is_internal': bool(row['is_internal'])
            }
            if row['is_internal']:
                if caller:
                    hangup_message_map[caller].append(record)
                if callee:
                    hangup_message_map[callee].append(record)
            else:
                if caller and not is_internal_number(caller):
                    hangup_message_map[caller].append(record)
                if callee and not is_internal_number(callee):
                    hangup_message_map[callee].append(record)
        
        # Ограничиваем историю
        for key in hangup_message_map:
            hangup_message_map[key] = hangup_message_map[key][:5]
            
        return rows
    except Exception as e:
        logging.error(f"Failed to load call history: {e}")
        return []

def format_phone_number(phone: str) -> str:
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
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except:
        return phone

def is_internal_number(number: str) -> bool:
    return number and re.match(r"^\d{3,4}$", number)

def get_last_call_info(external_number: str) -> str:
    if not external_number or is_internal_number(external_number):
        return ""
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT caller, callee, start_time, call_status, call_type, is_internal
            FROM call_history
            WHERE (caller = ? OR callee = ?) AND is_internal = 0
            ORDER BY start_time DESC
            LIMIT 5
        ''', (external_number, external_number))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return ""
        
        call_count = len(rows)
        last_call = rows[0]
        
        # Форматируем время последнего звонка
        last_timestamp = datetime.fromisoformat(last_call['start_time'])
        last_timestamp = last_timestamp.replace(hour=(last_timestamp.hour + 3) % 24)
        formatted_date = last_timestamp.strftime("%d.%m.%Y %H:%M")
        
        caller = last_call['caller']
        callee = last_call['callee']
        call_status = last_call['call_status']
        call_type = last_call['call_type']
        is_internal = last_call['is_internal']
        
        # Определяем направление звонка
        if caller == external_number:
            direction = "outgoing"
            manager = callee
            client = caller
        else:
            direction = "incoming"
            manager = caller
            client = callee
        
        # Форматируем текст
        status_text = ""
        if direction == "incoming":
            if call_status == 2:
                status_text = f"✅ 💰 ➡️ ☎️{manager}"
            elif call_status == 1:
                status_text = "❌ 💰🙅‍♂️"
            else:
                status_text = f"❌ 💰 ➡️ ☎️{manager}"
        else:
            if call_status == 2:
                status_text = f"✅ ☎️{manager} ➡️ 💰"
            else:
                status_text = f"❌ ☎️{manager} ➡️ 💰"
        
        return f"🛎️ {call_count}\nПоследний: {formatted_date}\n{status_text}"
    
    except Exception as e:
        logging.error(f"Failed to get last call info: {e}")
        return ""

@app.on_event("startup")
async def startup_tasks():
    logging.info("Starting application...")
    load_call_history()
    
    async def resend_loop():
        while True:
            await asyncio.sleep(10)
            for uid in list(active_bridges.keys()):
                msg_id = bridge_store.get(uid)
                if not msg_id:
                    continue
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, msg_id)
                    txt = active_bridges[uid]["text"]
                    caller = active_bridges[uid].get("cli")
                    callee = active_bridges[uid].get("op")
                    is_internal = is_internal_number(caller) and is_internal_number(callee)
                    reply_id = get_relevant_hangup_message_id(caller, callee, is_internal) if not is_internal else None
                    token = dial_cache.get(uid, {}).get("token", "")
                    
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=txt,
                        reply_to_message_id=reply_id,
                        parse_mode='HTML'
                    ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
                    
                    bridge_store[uid] = sent.message_id
                    save_telegram_message(sent.message_id, "bridge_resend", token, caller, callee, is_internal)
                except Exception as e:
                    logging.error(f"Failed to resend bridge message: {e}")
    
    asyncio.create_task(resend_loop())

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
    phone = format_phone_number(raw_phone)
    call_type = int(data.get("CallType", 0))
    token = data.get("Token", "")
    
    save_asterisk_event(et, uid, token, data)
    
    is_internal = call_type == 2 or (is_internal_number(raw_phone) and all(is_internal_number(e) for e in data.get("Extensions", [])))
    
    if et == "start":
        exts = data.get("Extensions", [])
        callee = exts[0] if exts else ""
        
        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
        else:
            display_phone = phone if not phone.startswith("+000") else "Номер не определен"
            txt = f"🛎️ Входящий звонок\n💰 {display_phone}"
            last_call_info = get_last_call_info(raw_phone)
            if last_call_info:
                txt += f"\n\n{last_call_info}"
        
        try:
            reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_internal) if not is_internal else None
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id,
                parse_mode='HTML'
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
            
            message_store[uid] = sent.message_id
            save_telegram_message(sent.message_id, et, token, raw_phone, callee, is_internal)
        except Exception as e:
            logging.error(f"Failed to send start message: {e}")
        
        return {"status": "sent"}
    
    elif et == "dial":
        exts = data.get("Extensions", [])
        if not raw_phone or not exts:
            return {"status": "ignored"}
        callee = exts[0]
        
        if uid in dial_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
            except Exception as e:
                logging.error(f"Failed to delete dial message: {e}")
        
        if is_internal:
            txt = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
        else:
            display_phone = phone if not phone.startswith("+000") else "Номер не определен"
            if call_type == 1:
                txt = f"⬆️ <b>Набираем номер</b>\n☎️ {', '.join(map(str, exts))} ➡️\n💰 {display_phone}"
            else:
                txt = f"🛎️ <b>Входящий разговор</b>\n💰 {display_phone} ➡️\n" + "\n".join(f"☎️ {e}" for e in exts)
            
            last_call_info = get_last_call_info(raw_phone if call_type != 1 else callee)
            if last_call_info:
                txt += f"\n\n{last_call_info}"
        
        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except Exception as e:
                logging.error(f"Failed to delete start message: {e}")
        
        try:
            reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_internal) if not is_internal else None
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id,
                parse_mode='HTML'
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
            
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": call_type, "extensions": exts, "caller": raw_phone, "token": token}
            dial_phone_to_uid[raw_phone] = uid
            if call_type == 1 and exts:
                for ext in exts:
                    dial_phone_to_uid[ext] = uid
            elif is_internal and callee:
                dial_phone_to_uid[callee] = uid
            
            save_telegram_message(sent.message_id, et, token, raw_phone, callee, is_internal)
        except Exception as e:
            logging.error(f"Failed to send dial message: {e}")
        
        return {"status": "sent"}
    
    elif et == "bridge":
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status = int(data.get("CallStatus", 0))
        
        if caller == "<unknown>" and connected == "<unknown>":
            return {"status": "ignored"}
        
        orig_uid = dial_phone_to_uid.get(caller) or dial_phone_to_uid.get(connected)
        if not orig_uid:
            return {"status": "ignored"}
        
        dial_data = dial_cache.get(orig_uid, {})
        call_type = dial_data.get("call_type", call_type)
        token = dial_data.get("token", token)
        
        if call_type == 1:
            orig_caller = connected
            orig_callee = dial_data.get("caller", caller)
        else:
            orig_caller = dial_data.get("caller", caller)
            orig_callee = connected
        
        key = tuple(sorted([orig_caller, orig_callee]))
        if key in bridge_seen:
            return {"status": "ignored"}
        
        if is_internal and caller != orig_caller:
            return {"status": "ignored"}
        
        try:
            await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(orig_uid, 0))
        except Exception as e:
            logging.error(f"Failed to delete dial message in bridge: {e}")
        
        if is_internal:
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
            display_callee = orig_callee if is_internal_number(orig_callee) else format_phone_number(orig_callee)
            if not is_internal_number(orig_callee) and display_callee.startswith("+000"):
                display_callee = "Номер не определен"
            
            manager_emoji = "☎️" if is_internal_number(orig_caller) else "💰"
            callee_emoji = "☎️" if is_internal_number(orig_callee) else "💰"
            txt = f"{pre}\n{manager_emoji} {orig_caller if is_internal_number(orig_caller) else formatted_cli} ➡️ {callee_emoji} {display_callee}"
            
            external_num = orig_callee if call_type == 1 else orig_caller
            last_call_info = get_last_call_info(external_num)
            if last_call_info:
                txt += f"\n\n{last_call_info}"
        
        try:
            reply_id = get_relevant_hangup_message_id(orig_caller, orig_callee, is_internal) if not is_internal else None
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=txt,
                reply_to_message_id=reply_id,
                parse_mode='HTML'
            ) if reply_id else await bot.send_message(TELEGRAM_CHAT_ID, txt, parse_mode='HTML')
            
            bridge_store[orig_uid] = sent.message_id
            bridge_seen.add(key)
            active_bridges[orig_uid] = {"text": txt, "cli": orig_caller, "op": orig_callee}
            save_telegram_message(sent.message_id, et, token, orig_caller, orig_callee, is_internal)
        except Exception as e:
            logging.error(f"Failed to send bridge message: {e}")
        
        return {"status": "sent"}
    
    elif et == "hangup":
        if not raw_phone:
            return {"status": "ignored"}
        
        caller = raw_phone
        exts = [e for e in data.get("Extensions", []) if e and str(e).strip()]
        callee = exts[0] if exts else ""
        if not callee and uid in active_bridges:
            callee = active_bridges.get(uid, {}).get("op", "")
        if not callee and uid in dial_cache:
            callee = dial_cache[uid].get("extensions", [""])[0]
        
        token = dial_cache.get(uid, {}).get("token", token)
        active_bridges.pop(uid, None)
        
        if caller and callee:
            key = tuple(sorted([caller, callee]))
            bridge_seen.discard(key)
        
        # Сохраняем в историю звонков
        save_call_history(
            caller=caller,
            callee=callee,
            start_time=data.get("StartTime"),
            end_time=data.get("EndTime"),
            call_status=int(data.get("CallStatus", -1)),
            call_type=int(data.get("CallType", -1)),
            is_internal=is_internal,
            token=token
        )
        
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
            logging.error(f"Hangup: Failed to calculate duration: {e}")
        
        external_number = caller if ct == 0 else (callee if callee else caller)
        reply_id = get_relevant_hangup_message_id(caller, callee, is_internal) if not is_internal else None
        
        if is_internal:
            if cs == 2:
                m = f"✅ Успешный внутренний звонок\n{caller} ➡️ {callee}\n⌛ {dur} 🔈 Запись"
            else:
                m = f"❌ Абонент не ответил\n{caller} ➡️ {callee}\n⌛ {dur}"
        else:
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
                except ValueError:
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
                except Exception:
                    sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
            else:
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
            
            save_telegram_message(sent.message_id, et, token, caller, callee, is_internal, cs, ct, exts)
        except Exception as e:
            logging.error(f"Failed to send hangup message: {e}")
            try:
                sent = await bot.send_message(TELEGRAM_CHAT_ID, m, parse_mode='HTML')
                save_telegram_message(sent.message_id, et, token, caller, callee, is_internal, cs, ct, exts)
            except Exception as e2:
                logging.error(f"Retry also failed: {e2}")
        
        return {"status": "sent"}
    
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
    except Exception as e:
        logging.error(f"Failed to send event message: {e}")
    
    return {"status": "sent"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)