import sqlite3
import logging
import json
from datetime import datetime
from config import DB_PATH

def init_database_tables():
    """
    Создаёт таблицы events и telegram_messages, если их нет, и добавляет поле token в events.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Проверяем поле token в events
    cursor.execute("PRAGMA table_info(events)")
    cols = [row[1] for row in cursor.fetchall()]
    if 'token' not in cols:
        cursor.execute("ALTER TABLE events ADD COLUMN token TEXT")
        logging.info("Added 'token' column to 'events' table")
    # Таблица telegram_messages
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
    conn.commit()
    conn.close()

def save_asterisk_event(event_type, unique_id, token, event_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    ts = datetime.now().isoformat()
    raw = json.dumps(event_data)
    cursor.execute(
        "INSERT INTO events (timestamp, event_type, unique_id, raw_json, token) VALUES (?, ?, ?, ?, ?)",
        (ts, event_type, unique_id, raw, token)
    )
    conn.commit()
    conn.close()
    logging.info(f"Saved Asterisk event {event_type} UID={unique_id}")

def save_telegram_message(message_id, event_type, token, caller, callee, is_internal, call_status=-1, call_type=-1, extensions=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    ts = datetime.now().isoformat()
    exts = json.dumps(extensions or [])
    cursor.execute(
        "INSERT INTO telegram_messages (message_id, event_type, token, caller, callee, is_internal, timestamp, call_status, call_type, extensions) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (message_id, event_type, token, caller, callee, 1 if is_internal else 0, ts, call_status, call_type, exts)
    )
    conn.commit()
    conn.close()
    logging.info(f"Saved Telegram message ID={message_id}")

def load_hangup_message_history():
    """
    Загружает из БД последние hangup-сообщения, чтобы восстановить историю для reply_to.
    """
    from collections import defaultdict
    hangup_map = defaultdict(list)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT message_id, caller, callee, is_internal, timestamp, call_status, call_type, extensions
        FROM telegram_messages
        WHERE event_type = 'hangup'
        ORDER BY timestamp DESC
        LIMIT 100
    ''')
    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        message_id, caller, callee, is_internal, ts, status, ctype, exts = row
        rec = {
            'message_id': message_id,
            'caller': caller,
            'callee': callee,
            'timestamp': ts,
            'call_status': status,
            'call_type': ctype,
            'extensions': json.loads(exts)
        }
        if is_internal:
            hangup_map[caller].append(rec)
            if callee:
                hangup_map[callee].append(rec)
        else:
            hangup_map[caller].append(rec)
    # Оставляем только 5 последних для каждого
    for k in hangup_map:
        hangup_map[k] = hangup_map[k][:5]
    return hangup_map
