import aiosqlite
import logging
import json
from datetime import datetime
from collections import defaultdict

from app.config import DB_PATH

# ───────── История hangup ─────────
hangup_message_map = defaultdict(list)

# ───────── Инициализация таблиц ─────────
async def init_database_tables():
    """
    Создаёт все таблицы и (при необходимости) колонку active в enterprises.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        # enterprises
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS enterprises (
          number     TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
          bot_token  TEXT NOT NULL,
          chat_id    TEXT NOT NULL,
          ip         TEXT NOT NULL,
          secret     TEXT NOT NULL,
          host       TEXT NOT NULL,
          created_at TEXT NOT NULL,
          name2      TEXT NOT NULL DEFAULT ''
        )
        """)
        # добавляем колонку active, если её нет
        cur = await conn.execute("PRAGMA table_info(enterprises)")
        cols = [r[1] for r in await cur.fetchall()]
        if 'active' not in cols:
            logging.info("Adding 'active' column to enterprises")
            await conn.execute(
                "ALTER TABLE enterprises ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            )

        # events
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
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_token ON events(token)")

        # telegram_messages
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
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tgmsg_token ON telegram_messages(token)")

        await conn.commit()
    logging.info("Initialized database tables")

# ───────── Сохранение Asterisk-события ─────────
async def save_asterisk_event(event_type: str, unique_id: str, token: str, data: dict):
    """
    Записывает сырое событие Asterisk в таблицу events.
    """
    ts = datetime.utcnow().isoformat()
    raw = json.dumps(data)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO events(timestamp, event_type, unique_id, raw_json, token) VALUES(?,?,?,?,?)",
            (ts, event_type, unique_id, raw, token)
        )
        await conn.commit()

# ───────── Загрузка истории hangup из БД ─────────
async def load_hangup_message_history(limit: int = 100):
    """
    Загружает последние сообщения 'hangup' из telegram_messages в память.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT message_id, caller, callee, is_internal, timestamp,
                   call_status, call_type, extensions
              FROM telegram_messages
             WHERE event_type = 'hangup'
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (limit,)
        )
        rows = await cur.fetchall()

    # строим hangup_message_map
    hangup_message_map.clear()
    for r in rows:
        rec = {
            'message_id':  r['message_id'],
            'caller':       r['caller'],
            'callee':       r['callee'],
            'is_internal':  bool(r['is_internal']),
            'timestamp':    r['timestamp'],
            'call_status':  r['call_status'],
            'call_type':    r['call_type'],
            'extensions':   json.loads(r['extensions'] or "[]")
        }
        # для внешних — по caller, для внутренних — по обоим
        hangup_message_map[rec['caller']].append(rec)
        if rec['is_internal']:
            hangup_message_map[rec['callee']].append(rec)

    logging.info("Loaded hangup history: %d records", len(rows))

# ───────── Сохранение Telegram-сообщения ─────────
async def save_telegram_message(
    message_id: int,
    event_type: str,
    token: str,
    caller: str,
    callee: str,
    is_internal: bool,
    call_status: int = -1,
    call_type: int = -1,
    extensions: list = None
):
    """
    Записывает историю отправленных сообщений в telegram_messages.
    """
    ts = datetime.utcnow().isoformat()
    exts = json.dumps(extensions or [])
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            INSERT INTO telegram_messages
              (message_id, event_type, token, caller, callee, is_internal,
               timestamp, call_status, call_type, extensions)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id, event_type, token,
                caller, callee,
                1 if is_internal else 0,
                ts, call_status, call_type, exts
            )
        )
        await conn.commit()
