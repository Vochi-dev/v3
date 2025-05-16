# app/services/events.py
import aiosqlite, sqlite3, logging, json
from datetime import datetime
from collections import defaultdict

from app.config import DB_PATH

# История hangup
hangup_message_map = defaultdict(list)

async def init_database_tables():
    """
    Создаёт таблицы и гарантирует, что в enterprises есть колонка active.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        # таблица enterprises (если отсутствует)
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
        # если нет колонки active — добавляем
        cur = await conn.execute("PRAGMA table_info(enterprises)")
        cols = [row[1] for row in await cur.fetchall()]
        if 'active' not in cols:
            logging.info("Adding 'active' column to enterprises")
            await conn.execute(
                "ALTER TABLE enterprises ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            )

        # остальные таблицы
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

# ... остальной код save_asterisk_event, load_hangup_message_history, save_telegram_message без изменений
