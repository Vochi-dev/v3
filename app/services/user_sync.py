# app/services/user_sync.py
import csv
import io
from typing import Set

import aiosqlite
from telegram import Bot

from app.config import DB_PATH
from app.services.db import get_enterprise_number_by_bot_token

async def sync_users_from_csv(file_bytes: bytes, bot_token: str) -> None:
    """
    Синхронизирует подписанных в боте пользователей с CSV:
    1) Парсит CSV как DictReader и собирает email из колонки 'email'.
    2) Берёт из telegram_users всех verified пользователей для данного bot_token.
    3) Для тех, кого нет в CSV, удаляет их из telegram_users и enterprise_users
       и отправляет уведомление об отзыве доступа.
    """
    # 1) Парсим CSV и собираем set(email)
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    new_emails: Set[str] = {
        row.get("email", "").strip().lower()
        for row in reader
        if row.get("email", "").strip()
    }

    # 2) Текущие подписанные в боте пользователи
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, email FROM telegram_users "
            "WHERE verified = 1 AND bot_token = ?",
            (bot_token,),
        ) as cur:
            rows = await cur.fetchall()

    # 3) Определяем, кого нужно удалить
    to_remove = [(r["tg_id"], r["email"]) for r in rows if r["email"].lower() not in new_emails]
    if not to_remove:
        return

    # 4) Удаляем их из БД и уведомляем через бот
    bot = Bot(token=bot_token)
    async with aiosqlite.connect(DB_PATH) as db:
        for tg_id, email in to_remove:
            # удаляем связь в enterprise_users
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
                # если уведомление не прошло — не критично
                pass
        await db.commit()
