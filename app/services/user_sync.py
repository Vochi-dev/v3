# app/services/user_sync.py
import csv
import io
from typing import List, Dict, Set

import aiosqlite
from telegram import Bot
from telegram.error import TelegramError

from app.config import DB_PATH


async def find_users_to_remove(file_bytes: bytes) -> List[Dict]:
    """
    Разбирает CSV и возвращает список пользователей, которых
    следует удалить (для показа администратору).
    Каждый элемент списка:
      {
        "tg_id": Optional[int],
        "email": str,
        "enterprise_name": Optional[str]
      }
    """
    # 1) Парсим CSV → множество email
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    csv_emails: Set[str] = set()
    for row in reader:
        email = (row.get("email") or "").strip().lower()
        if email:
            csv_emails.add(email)

    to_remove: List[Dict] = []

    # 2) Берём из БД все email_users + привязки к enterprise и telegram
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
              eu.email         AS email,
              ent.name         AS enterprise_name,
              tu.tg_id         AS tg_id
            FROM email_users eu
            LEFT JOIN enterprises ent
              ON eu.number = ent.number
            LEFT JOIN telegram_users tu
              ON eu.email = tu.email
            """
        ) as cur:
            rows = await cur.fetchall()

    # 3) Кого нет в CSV — добавляем в to_remove
    for row in rows:
        if row["email"].strip().lower() not in csv_emails:
            to_remove.append({
                "tg_id": row["tg_id"],
                "email": row["email"],
                "enterprise_name": row["enterprise_name"],
            })

    return to_remove


async def perform_sync(file_bytes: bytes) -> None:
    """
    Удаляет из email_users (и, если пользователь был привязан —
    из telegram_users и enterprise_users) всех, кого вернул
    find_users_to_remove, и уведомляет их в Telegram.
    """
    users = await find_users_to_remove(file_bytes)
    if not users:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        for u in users:
            tg_id = u["tg_id"]
            email = u["email"]

            # 1) если пользователь был проверен и привязан — найдём его enterprise_users
            bot_token = None
            if tg_id is not None:
                async with db.execute(
                    """
                    SELECT e.bot_token
                    FROM enterprise_users eu
                    JOIN enterprises e ON eu.enterprise_id = e.number
                    WHERE eu.telegram_id = ?
                    """,
                    (tg_id,),
                ) as cur:
                    row = await cur.fetchone()
                if row:
                    bot_token = row["bot_token"]

                # 2) удаляем из enterprise_users и telegram_users
                await db.execute(
                    "DELETE FROM enterprise_users WHERE telegram_id = ?",
                    (tg_id,),
                )
                await db.execute(
                    "DELETE FROM telegram_users WHERE tg_id = ?",
                    (tg_id,),
                )

            # 3) удаляем из email_users
            await db.execute(
                "DELETE FROM email_users WHERE email = ?",
                (email,),
            )

            # 4) уведомляем пользователя, если знаем его tg_id и bot_token
            if tg_id and bot_token:
                try:
                    bot = Bot(token=bot_token)
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            "🚫 Ваш доступ отозван — "
                            "ваш e-mail больше не значится в актуальном CSV."
                        )
                    )
                except TelegramError:
                    pass

        await db.commit()
