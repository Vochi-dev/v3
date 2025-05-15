# app/services/user_sync.py
import csv
import io
import datetime as dt
import aiosqlite
from telegram import Bot
from typing import List

from app.config import DB_PATH
from app.services.db import get_enterprise_number_by_bot_token

async def sync_users_from_csv(file_bytes: bytes, bot_token: str) -> None:
    """
    Синхронизирует список email из CSV с зарегистрированными в боте пользователями:
    1) Парсит CSV из bytes → список email.
    2) Получает из telegram_users всех verified пользователей для данного bot_token.
    3) Для тех, кого нет в CSV, удаляет запись из telegram_users и enterprise_users,
       отправляет уведомление «Ваш доступ отозван».
    """
    # 1) распарсиваем CSV
    text = file_bytes.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(text))
    new_emails = {row[0].strip().lower() for row in reader if row}
    # 2) текущие пользователи в боте
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, email FROM telegram_users WHERE verified = 1 AND bot_token = ?",
            (bot_token,),
        ) as cur:
            rows = await cur.fetchall()
    # 3) определяем, кого удалить
    to_remove = [(r["tg_id"], r["email"]) for r in rows if r["email"] not in new_emails]
    if not to_remove:
        return

    # 4) удаляем их и уведомляем
    bot = Bot(token=bot_token)
    async with aiosqlite.connect(DB_PATH) as db:
        for tg_id, email in to_remove:
            # удаляем из enterprise_users
            await db.execute(
                "DELETE FROM enterprise_users WHERE telegram_id = ?",
                (tg_id,),
            )
            # удаляем из telegram_users
            await db.execute(
                "DELETE FROM telegram_users WHERE tg_id = ?",
                (tg_id,),
            )
            # уведомляем пользователя
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="🚫 Ваш доступ к боту отозван (email больше не значится в CSV)."
                )
            except Exception:
                pass
        await db.commit()
