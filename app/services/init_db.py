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
    bot_token  TEXT    NOT NULL
);

/* ---- email_users ---- */
CREATE TABLE IF NOT EXISTS email_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id INTEGER NOT NULL,
    email         TEXT    NOT NULL,
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
        # создаём схему
        await db.executescript(SQL_SCHEMA)

        # ————————————————
        # upsert test enterprise
        # ————————————————
        # проверяем, есть ли уже группа с таким bot_token
        cur = await db.execute(
            "SELECT id FROM enterprises WHERE bot_token = ?",
            (settings.TELEGRAM_BOT_TOKEN,)
        )
        ent = await cur.fetchone()
        if ent:
            # обновляем имя, если отличается
            await db.execute(
                "UPDATE enterprises SET name = ? WHERE id = ?",
                ('Test Enterprise', ent[0])
            )
        else:
            # вставляем новую запись
            await db.execute(
                "INSERT INTO enterprises (name, bot_token) VALUES (?, ?)",
                ('Test Enterprise', settings.TELEGRAM_BOT_TOKEN)
            )

        # ————————————————
        # upsert test email user
        # ————————————————
        # сначала найдём id этого предприятия
        cur2 = await db.execute(
            "SELECT id FROM enterprises WHERE bot_token = ?",
            (settings.TELEGRAM_BOT_TOKEN,)
        )
        ent_row = await cur2.fetchone()
        if ent_row:
            ent_id = ent_row[0]
            # проверяем, нет ли уже этого e-mail
            cur3 = await db.execute(
                "SELECT 1 FROM email_users WHERE email = ?",
                ('user@example.com',)
            )
            if not await cur3.fetchone():
                # если нет — вставляем
                await db.execute(
                    "INSERT INTO email_users (enterprise_id, email) VALUES (?, ?)",
                    (ent_id, 'user@example.com')
                )

        # сохраняем все изменения
        await db.commit()

    print(f"✅ База {settings.DB_PATH} инициализирована/обновлена")

if __name__ == "__main__":
    asyncio.run(main())
