# app/services/email_verification.py
# -*- coding: utf-8 -*-
"""
Вся логика, связанная с подтверждением e-mail:
• генерация токена
• проверка, что e-mail относится к предприятию
• проверка, что e-mail ещё не активирован в другом боте
• upsert в telegram_users
• отправка письма
• подтверждение токена (mark_verified)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

import aiosqlite

from app.config import (
    DB_PATH,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASS,
    VERIFY_URL_BASE,
)

# --------------------------------------------------------------------- #
# 1) Утилиты и проверки                                                 #
# --------------------------------------------------------------------- #
TOKEN_TTL_MINUTES = 30              # токен живёт 30 минут


def random_token(nbytes: int = 16) -> str:
    """URL-friendly токен для ссылок."""
    return secrets.token_urlsafe(nbytes)


async def email_exists_for_enterprise(email: str, enterprise_id: int) -> bool:
    """Есть ли такой e-mail в email_users именно для данного предприятия?"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT 1
              FROM email_users
             WHERE email = ?
               AND enterprise_id = ?
            """,
            (email, enterprise_id),
        ) as cur:
            return await cur.fetchone() is not None


async def email_already_linked_to_another_bot(
    email: str, enterprise_id: int
) -> bool:
    """
    True, если e-mail уже verified в telegram_users
    и enterprise_id там другой.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT enterprise_id
              FROM telegram_users
             WHERE email = ?
               AND verified = 1
            """,
            (email,),
        ) as cur:
            row = await cur.fetchone()
            return row is not None and row["enterprise_id"] != enterprise_id


async def upsert_telegram_user(
    telegram_id: int,
    enterprise_id: int,
    email: str,
    token: str,
) -> None:
    """Вставляем или обновляем запись о Telegram-пользователе."""
    now = dt.datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO telegram_users (telegram_id,
                                        enterprise_id,
                                        email,
                                        token,
                                        verified,
                                        updated_at)
                 VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(email) DO UPDATE
                  SET telegram_id  = excluded.telegram_id,
                      enterprise_id= excluded.enterprise_id,
                      token        = excluded.token,
                      verified     = 0,
                      updated_at   = excluded.updated_at
            """,
            (telegram_id, enterprise_id, email, token, now),
        )
        await db.commit()


# --------------------------------------------------------------------- #
# 2) Отправка письма с ссылкой                                          #
# --------------------------------------------------------------------- #
async def send_verification_email(email: str, token: str) -> None:
    """
    Формирует ссылку VERIFY_URL_BASE?token=... и шлёт письмо.
    SMTP-отправка выполняется в thread-pool, чтобы не блокировать asyncio.
    """

    def _send_sync() -> None:
        link = f"{VERIFY_URL_BASE}?token={token}"

        msg = EmailMessage()
        msg["Subject"] = "Подтверждение доступа к Telegram-боту"
        msg["From"] = SMTP_USER
        msg["To"] = email
        msg.set_content(
            f"Здравствуйте!\n\n"
            f"Для подтверждения доступа к корпоративному Telegram-боту "
            f"перейдите по ссылке:\n\n{link}\n\n"
            f"Ссылка действительна {TOKEN_TTL_MINUTES} минут."
        )

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)

    await asyncio.to_thread(_send_sync)


# --------------------------------------------------------------------- #
# 3) Подтверждение токена                                               #
# --------------------------------------------------------------------- #
async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    """
    Проверяем токен: если валиден и не устарел — ставим verified=1,
    обнуляем token, возвращаем (True, telegram_id).
    При ошибке возвращаем (False, None).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            """
            SELECT id, telegram_id, updated_at, verified
              FROM telegram_users
             WHERE token = ?
            """,
            (token,),
        ) as cur:
            row = await cur.fetchone()

        # токен не найден
        if row is None:
            return False, None

        # уже подтверждён раньше
        if row["verified"]:
            return True, row["telegram_id"]

        # проверяем TTL
        updated_at = dt.datetime.fromisoformat(row["updated_at"])
        if dt.datetime.utcnow() - updated_at > dt.timedelta(minutes=TOKEN_TTL_MINUTES):
            return False, None

        # всё хорошо – отмечаем verified
        await db.execute(
            """
            UPDATE telegram_users
               SET verified = 1,
                   token    = NULL
             WHERE id = ?
            """,
            (row["id"],),
        )
        await db.commit()

    return True, row["telegram_id"]
