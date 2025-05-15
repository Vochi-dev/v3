# app/services/user_sync.py
import csv
import io
from typing import Dict, List, Set

import aiosqlite
from telegram import Bot
from telegram.error import TelegramError

from app.config import DB_PATH

async def find_users_to_remove(file_bytes: bytes) -> List[Dict]:
    """
    Возвращает список пользователей, чьи email есть в таблице telegram_users (и verified=1),
    но отсутствуют в загружаемом CSV (глобально).
    Элемент списка:
      {
        "tg_id": int,
        "email": str,
        "bot_token": Optional[str]
      }
    """
    # Парсим CSV → множество email
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    csv_emails: Set[str] = {
        row.get("email", "").strip().lower()
        for row in reader
        if row.get("email")
    }

    to_remove: List[Dict] = []

    # Извлекаем всех подтверждённых телеграм-пользователей и их bot_token
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
              tu.tg_id,
              tu.email,
              e.bot_token
            FROM telegram_users tu
            LEFT JOIN enterprise_users eu ON eu.telegram_id = tu.tg_id
            LEFT JOIN enterprises e    ON eu.enterprise_id = e.number
            WHERE tu.verified = 1
            """
        ) as cur:
            rows = await cur.fetchall()

        for r in rows:
            email = r["email"].strip().lower()
            if email and email not in csv_emails:
                to_remove.append({
                    "tg_id":     r["tg_id"],
                    "email":     r["email"],
                    "bot_token": r["bot_token"],
                })

    return to_remove

async def perform_sync(file_bytes: bytes) -> None:
    """
    Удаляет всех пользователей из telegram_users/enterprise_users,
    которых вернул find_users_to_remove, и шлёт им уведомления.
    """
    users = await find_users_to_remove(file_bytes)
    if not users:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        for u in users:
            tg_id     = u["tg_id"]
            bot_token = u.get("bot_token")

            # Удаляем из enterprise_users и telegram_users
            await db.execute(
                "DELETE FROM enterprise_users WHERE telegram_id = ?",
                (tg_id,),
            )
            await db.execute(
                "DELETE FROM telegram_users WHERE tg_id = ?",
                (tg_id,),
            )

            # Отправляем уведомление через соответствующий бот
            if bot_token:
                try:
                    bot = Bot(token=bot_token)
                    await bot.send_message(
                        chat_id=tg_id,
                        text="🚫 Ваш доступ отозван — ваш e-mail больше не найден в новом CSV."
                    )
                except TelegramError:
                    # Если не удалось доставить — молча пропускаем
                    pass

        await db.commit()
