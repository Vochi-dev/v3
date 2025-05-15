# app/services/add_email_user.py
# -*- coding: utf-8 -*-
"""
Утилита-однострочник: добавляет e-mail в таблицу email_users
для нужного предприятия (enterprises.number).

Запуск из корня проекта:

    python3 -m app.services.add_email_user 0201 ivan.petrov@example.com "Ivan Petrov"

Параметры:
    1) number предприятия   (обязательный)
    2) e-mail               (обязательный)
    3) имя / ФИО            (необязательное — можно пустую строку)
"""

import sys
import asyncio
import aiosqlite

from app.config import DB_PATH


USAGE = (
    "Использование:\n"
    "  python3 -m app.services.add_email_user <number> <email> [name]\n"
)


async def main() -> None:
    if len(sys.argv) < 3:
        print(USAGE, file=sys.stderr)
        sys.exit(1)

    number = sys.argv[1]
    email  = sys.argv[2].lower().strip()
    name   = sys.argv[3] if len(sys.argv) > 3 else ""

    async with aiosqlite.connect(DB_PATH) as db:
        # проверяем, что предприятие существует
        cur = await db.execute(
            "SELECT 1 FROM enterprises WHERE number = ?", (number,)
        )
        if await cur.fetchone() is None:
            print(f"⛔️ Предприятие {number} не найдено.", file=sys.stderr)
            sys.exit(1)

        # вставляем e-mail с дефолтными правами (0)
        await db.execute(
            """
            INSERT INTO email_users (
                number, email, name, right_all, right_1, right_2
            )
            VALUES (?, ?, ?, 0, 0, 0)
            ON CONFLICT(email) DO NOTHING
            """,
            (number, email, name),
        )
        await db.commit()

    print(f"✅ E-mail {email} добавлен для предприятия {number}.")

if __name__ == "__main__":
    asyncio.run(main())
