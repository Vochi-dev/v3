# app/services/email_verification.py
# -*- coding: utf-8 -*-
"""
Логика подтверждения e-mail с учётом bot_token и логированием попыток отправки:
• random_token
• проверка email
• upsert_telegram_user сохраняет bot_token (ON CONFLICT по tg_id)
• send_verification_email — отправка письма через SMTP_SSL (порт 465) или STARTTLS (порт 587),
  плюс запись в email_logs
• mark_verified — подтв. токена + запись в enterprise_users
"""

import asyncio
import datetime as dt
import secrets
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

import aiosqlite
import sqlite3

from app.config import (
    DB_PATH,
    EMAIL_HOST, EMAIL_PORT,
    EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
    EMAIL_FROM, VERIFY_URL_BASE,
)
from app.services.db import get_enterprise_number_by_bot_token

TOKEN_TTL_MINUTES = 30


def random_token(nbytes: int = 16) -> str:
    """Генерирует URL-дружественный токен."""
    return secrets.token_urlsafe(nbytes)


async def email_exists(email: str) -> bool:
    """Проверяет, есть ли email в таблице email_users."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM email_users WHERE email = ?", (email,)
        ) as cur:
            return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    """Проверяет, подтверждён ли email в telegram_users."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM telegram_users WHERE email = ? AND verified = 1", (email,)
        ) as cur:
            return await cur.fetchone() is not None


async def upsert_telegram_user(
    tg_id: int, email: str, token: str, bot_token: str
) -> None:
    """
    Добавляет или обновляет запись в telegram_users по ключу tg_id:
    • tg_id, email, token, verified=0, added_at=CURRENT_TIMESTAMP, bot_token
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO telegram_users (
                tg_id, email, token, verified, added_at, bot_token
            ) VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                email      = excluded.email,
                token      = excluded.token,
                verified   = 0,
                added_at   = CURRENT_TIMESTAMP,
                bot_token  = excluded.bot_token
            """,
            (tg_id, email, token, bot_token),
        )
        await db.commit()


async def send_verification_email(email: str, token: str) -> None:
    """
    Формирует ссылку VERIFY_URL_BASE/<token>, отправляет письмо
    через SMTP_SSL (порт 465) или STARTTLS (порт 587),
    и логирует результат в таблице email_logs.
    """
    link = f"{VERIFY_URL_BASE}/{token}"

    def _send_sync():
        msg = EmailMessage()
        msg["Subject"] = "Подтверждение доступа к Telegram-боту"
        msg["From"] = EMAIL_FROM or EMAIL_HOST_USER
        msg["To"] = email
        msg.set_content(
            f"Здравствуйте!\n\n"
            f"Чтобы подтвердить доступ к боту, перейдите по ссылке:\n"
            f"{link}\n\n"
            f"Ссылка действует {TOKEN_TTL_MINUTES} минут."
        )

        try:
            if EMAIL_PORT == 465:
                smtp_class = smtplib.SMTP_SSL
                smtp_args = (EMAIL_HOST, EMAIL_PORT)
                start_tls = False
            else:
                smtp_class = smtplib.SMTP
                smtp_args = (EMAIL_HOST, EMAIL_PORT)
                start_tls = True

            with smtp_class(*smtp_args) as smtp:
                smtp.ehlo()
                if start_tls:
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
                smtp.send_message(msg)

            status, error = "sent", None
        except Exception as e:
            status, error = "error", str(e)

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO email_logs (email, token, status, error_msg) VALUES (?, ?, ?, ?)",
            (email, token, status, error),
        )
        conn.commit()
        conn.close()

        if status == "error":
            raise RuntimeError(f"Email send error: {error}")

    asyncio.run(asyncio.to_thread(_send_sync))


async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    """
    Подтверждает токен:
    • ищет запись в telegram_users по token (вместе с bot_token)
    • проверяет TTL
    • обновляет verified и очищает token
    • записывает в enterprise_users на основании bot_token
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, email, added_at, verified, bot_token FROM telegram_users WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return False, None
        if row["verified"] == 1:
            return True, row["tg_id"]

        try:
            added_at = dt.datetime.strptime(row["added_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            added_at = dt.datetime.fromisoformat(row["added_at"])

        if dt.datetime.utcnow() - added_at > dt.timedelta(minutes=TOKEN_TTL_MINUTES):
            return False, None

        await db.execute(
            "UPDATE telegram_users SET verified = 1, token = NULL WHERE token = ?",
            (token,),
        )
        await db.commit()

    enterprise_number = await get_enterprise_number_by_bot_token(row["bot_token"])
    if enterprise_number:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO enterprise_users
                  (enterprise_id, telegram_id, username, requested_at, status)
                VALUES (?, ?, NULL, CURRENT_TIMESTAMP, 'approved')
                """,
                (enterprise_number, row["tg_id"]),
            )
            await db.commit()

    return True, row["tg_id"]
