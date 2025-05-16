# app/services/events.py

import logging
import aiosqlite
from datetime import datetime

DB_PATH = None  # Подхватывается из app.config

async def init_database_tables():
    """
    Создаёт необходимые таблицы (events, telegram_messages) и проверяет,
    что в таблице enterprises есть поле active.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # existing tables...
        await db.execute("""
            CREATE TABLE IF NOT EXISTS enterprises (
              number     TEXT    PRIMARY KEY,
              name       TEXT    NOT NULL,
              bot_token  TEXT    NOT NULL,
              chat_id    TEXT    NOT NULL,
              ip         TEXT    NOT NULL,
              secret     TEXT    NOT NULL,
              host       TEXT    NOT NULL,
              created_at TEXT    NOT NULL,
              name2      TEXT    NOT NULL DEFAULT ''
            )
        """)
        # проверяем наличие столбца active
        cur = await db.execute("PRAGMA table_info(enterprises)")
        cols = [row[1] for row in await cur.fetchall()]
        if 'active' not in cols:
            logging.info("Adding 'active' column to enterprises")
            await db.execute("ALTER TABLE enterprises ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        await db.commit()
    logging.info("Initialized database tables")
