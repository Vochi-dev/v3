# app/services/user_sync.py
import csv
import io
from typing import Dict, Set

import aiosqlite
from telegram import Bot

from app.config import DB_PATH

async def sync_users_from_csv(file_bytes: bytes) -> None:
    """
    Синхронизирует подписки всех предприятий по единому CSV:
    CSV должен содержать колонку 'number' (enterprise number) и 'email'.
    Логика:
    1) Парсим CSV → mapping: number -> set(email).
    2) Из БД читаем все предприятия (number, bot_token).
    3) Для каждого предприятия:
       a) Получаем подписанных пользователей (telegram_id + email).
       b) Если чей-то email не входит в set для этого number — удаляем его
          из enterprise_users и telegram_users и уведомляем через бот.
    """
    # 1) Парсим CSV
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    csv_map: Dict[str, Set[str]] = {}
    for row in reader:
        number = (row.get("number") or "").strip()
        email  = (row.get("email")  or "").strip().lower()
        if not number or not email:
            continue
        csv_map.setdefault(number, set()).add(email)

    # 2) Читаем все предприятия из БД
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT number, bot_token FROM enterprises") as cur:
            ent_rows = await cur.fetchall()

    # 3) Для каждого предприятия — синхронизируем
    for ent in ent_rows:
        number   = ent["number"]
        bot_token = ent["bot_token"]
        allowed_emails = csv_map.get(number, set())

        # a) получаем подписавшихся пользователей
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

        # b) кто лишний?
        to_remove = [(r["tg_id"], r["email"]) for r in subs if r["email"].lower() not in allowed_emails]
        if not to_remove:
            continue

        # c) удаляем и уведомляем
        bot = Bot(token=bot_token)
        async with aiosqlite.connect(DB_PATH) as db:
            for tg_id, email in to_remove:
                # удаляем из enterprise_users
                await db.execute(
                    "DELETE FROM enterprise_users WHERE telegram_id = ? AND enterprise_id = ?",
                    (tg_id, number),
                )
                # удаляем из telegram_users
                await db.execute(
                    "DELETE FROM telegram_users WHERE tg_id = ?",
                    (tg_id,),
                )
                # уведомляем
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"🚫 Ваш доступ к предприятию {number} ({ent['number']}) отозван — "
                            "ваш e-mail больше не значится в актуальном CSV."
                        )
                    )
                except Exception:
                    pass
            await db.commit()
