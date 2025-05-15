# app/services/events.py

import aiosqlite
import sqlite3
import logging
import json
from datetime import datetime
from collections import defaultdict

from app.config import DB_PATH

# Глобальная карта для хранения истории hangup
hangup_message_map = defaultdict(list)

async def init_database_tables():
    """
    Асинхронно создаёт таблицы events и telegram_messages, если их нет,
    и добавляет индексы для быстрого поиска по token.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        # таблица для сырых событий
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                unique_id TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                token TEXT
            )
        """)
        # индекс для быстрого поиска по token в events
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_token
            ON events(token)
        """)
        # таблица для сообщений Telegram
        await conn.execute("""
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
        """)
        # индекс для быстрого поиска по token в telegram_messages
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tgmsg_token
            ON telegram_messages(token)
        """)
        await conn.commit()
    logging.info("Initialized database tables")

async def save_asterisk_event(event_type: str, unique_id: str, token: str, event_data: dict):
    """
    Асинхронно сохраняет событие Asterisk в таблицу events.
    """
    ts = datetime.utcnow().isoformat()
    raw = json.dumps(event_data)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO events (timestamp, event_type, unique_id, raw_json, token) VALUES (?, ?, ?, ?, ?)",
            (ts, event_type, unique_id, raw, token)
        )
        await conn.commit()
    logging.info(f"Saved Asterisk event {event_type} UID={unique_id}")

async def load_hangup_message_history(limit: int = 100):
    """
    Асинхронно загружает из БД последние hangup-сообщения для reply_to.
    """
    hm = defaultdict(list)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT message_id, caller, callee, is_internal, timestamp, 
                   call_status, call_type, extensions
              FROM telegram_messages
             WHERE event_type = 'hangup'
             ORDER BY timestamp DESC
             LIMIT ?
        """, (limit,))
        rows = await cur.fetchall()

    for row in rows:
        rec = {
            'message_id': row['message_id'],
            'caller': row['caller'],
            'callee': row['callee'],
            'timestamp': row['timestamp'],
            'call_status': row['call_status'],
            'call_type': row['call_type'],
            'extensions': json.loads(row['extensions']),
        }
        if row['is_internal']:
            hm[row['caller']].append(rec)
            if row['callee']:
                hm[row['callee']].append(rec)
        else:
            hm[row['caller']].append(rec)

    # обрезаем до 5 записей на ключ
    for key in hm:
        hangup_message_map[key] = hm[key][:5]
    logging.info("Loaded hangup history")
    return hangup_message_map

def save_telegram_message(
    message_id: int,
    event_type: str,
    token: str,
    caller: str,
    callee: str,
    is_internal: bool,
    call_status: int = -1,
    call_type: int = -1,
    extensions=None
):
    """
    Синхронно сохраняет сообщение Telegram в таблицу telegram_messages.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    ts = datetime.utcnow().isoformat()
    exts = json.dumps(extensions or [])
    cursor.execute(
        "INSERT INTO telegram_messages "
        "(message_id, event_type, token, caller, callee, is_internal, timestamp, call_status, call_type, extensions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            event_type,
            token,
            caller,
            callee,
            1 if is_internal else 0,
            ts,
            call_status,
            call_type,
            exts,
        )
    )
    conn.commit()
    conn.close()
    logging.info(f"Saved Telegram message ID={message_id}")
