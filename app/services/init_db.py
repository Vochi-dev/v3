# app/services/init_db.py
# -*- coding: utf-8 -*-
"""
Однократный скрипт: создаёт все нужные таблицы, если их ещё нет,
и добавляет тестовое предприятие + e-mail-пользователя.
Запускается:  python -m app.services.init_db
"""

import asyncio
from pathlib import Path
import aiosqlite

from app.config import settings

SQL_SCHEMA = """
PRAGMA journal_mode=WAL;

/* ---- enterprises ---- */
CREATE TABLE IF NOT EXISTS enterprises (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    bot_token  TEXT    UNIQUE NOT NULL
);

/* ---- email_users ---- */
CREATE TABLE IF NOT EXISTS email_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id INTEGER NOT NULL,
    email         TEXT    UNIQUE NOT NULL,
    FOREIGN KEY (enterprise_id) REFERENCES enterprises(id)
);

/* ---- telegram_users ---- */
CREATE TABLE IF NOT EXISTS telegram_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id   INTEGER,
    enterprise_id INTEGER NOT NULL,
    email         TEXT    NOT NULL,
    token         TEXT,
    verified      INTEGER DEFAULT 0,   -- 0/1
    updated_at    TEXT,
    FOREIGN KEY (enterprise_id) REFERENCES enterprises(id)
);
"""

async def main() -> None:
    # создаём папку под БД, если нужно
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.executescript(SQL_SCHEMA)

        # --- upsert test enterprise ---
        await db.execute(
            """
            INSERT INTO enterprises (name, bot_token)
                 VALUES ('Test Enterprise', ?)
            ON CONFLICT(bot_token) DO UPDATE SET name = excluded.name
            """,
            (settings.TELEGRAM_BOT_TOKEN,),
        )

        # --- upsert test email user ---
        await db.execute(
            """
            INSERT INTO email_users (enterprise_id, email)
            SELECT id, 'user@example.com'
              FROM enterprises
             WHERE bot_token = ?
            ON CONFLICT(email) DO NOTHING
            """,
            (settings.TELEGRAM_BOT_TOKEN,),
        )

        await db.commit()

    print(f"✅ База {settings.DB_PATH} инициализирована/обновлена")


if __name__ == "__main__":
    asyncio.run(main())
