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
    Разбирает CSV и возвращает список пользователей, которых
    следует удалить (для показа администратору).
    Каждый элемент списка:
      {
        "tg_id": int,
        "email": str,
        "enterprise_name": str
      }
    """
    # 1) Парсим CSV → mapping: number -> set(emails)
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    csv_map: Dict[str, Set[str]] = {}
    for row in reader:
        number = (row.get("number") or "").strip()
        email  = (row.get("email")  or "").strip().lower()
        if number and email:
            csv_map.setdefault(number, set()).add(email)

    to_remove: List[Dict] = []

    # 2) Получаем список предприятий (number, bot_token, name)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT number, bot_token, name FROM enterprises") as cur:
            enterprises = await cur.fetchall()

    # 3) Для каждого предприятия проверяем подписки
    for ent in enterprises:
        number       = ent["number"]
        enterprise_name = ent["name"]
        allowed_emails = csv_map.get(number, set())

        # 3a) получаем подписанных пользователей этого предприятия
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT u.telegram_id AS tg_id, tu.email
                FROM enterprise_users u
                JOIN telegram_users tu ON u.telegram_id = tu.tg_id
                WHERE u.enterprise_id = ?
                """,
                (number,),
            ) as cur:
                subs = await cur.fetchall()

        # 3b) кого нет в CSV — в to_remove
        for r in subs:
            if r["email"].strip().lower() not in allowed_emails:
                to_remove.append({
                    "tg_id": r["tg_id"],
                    "email": r["email"],
                    "enterprise_name": enterprise_name,
                })

    return to_remove


async def perform_sync(file_bytes: bytes) -> None:
    """
    Удаляет из БД и уведомляет всех пользователей,
    которых определил find_users_to_remove().
    """
    users = await find_users_to_remove(file_bytes)
    if not users:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        for u in users:
            tg_id = u["tg_id"]
            email = u["email"]

            # 1) Находим bot_token для этого tg_id
            async with db.execute(
                """
                SELECT e.bot_token
                FROM enterprise_users u
                JOIN enterprises e ON u.enterprise_id = e.number
                WHERE u.telegram_id = ?
                """,
                (tg_id,),
            ) as cur:
                row = await cur.fetchone()
            bot_token = row["bot_token"] if row else None

            # 2) Удаляем из enterprise_users и telegram_users
            await db.execute(
                "DELETE FROM enterprise_users WHERE telegram_id = ?",
                (tg_id,),
            )
            await db.execute(
                "DELETE FROM telegram_users WHERE tg_id = ?",
                (tg_id,),
            )

            # 3) Уведомляем пользователя через его бот
            if bot_token:
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
