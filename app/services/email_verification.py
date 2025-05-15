# app/services/email_verification.py
# -*- coding: utf-8 -*-
"""
Логика подтверждения e-mail:
• генерация токена
• проверка наличия e-mail
• upsert в telegram_users
• отправка письма (с учётом TLS/STARTTLS)
• mark_verified — подтв. токена + запись в enterprise_users
"""

import asyncio
import datetime as dt
import secrets
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

import aiosqlite

from app.config import (
    DB_PATH,
    EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, EMAIL_FROM,
    VERIFY_URL_BASE, TELEGRAM_BOT_TOKEN,
)
from app.services.db import get_enterprise_number_by_bot_token

TOKEN_TTL_MINUTES = 30


def random_token(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


async def email_exists(email: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM email_users WHERE email = ?", (email,)
        ) as cur:
            return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM telegram_users WHERE email = ? AND verified = 1", (email,)
        ) as cur:
            return await cur.fetchone() is not None


async def upsert_telegram_user(tg_id: int, email: str, token: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO telegram_users (
                tg_id, email, token, verified, added_at
            ) VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET
                tg_id    = excluded.tg_id,
                token    = excluded.token,
                verified = 0,
                added_at = CURRENT_TIMESTAMP
            """,
            (tg_id, email, token),
        )
        await db.commit()


async def send_verification_email(email: str, token: str) -> None:
    """
    Формирует ссылку VERIFY_URL_BASE/<token> и отправляет письмо.
    Для EMAIL_USE_TLS=True и порта 587 — используем STARTTLS,
    иначе — SMTP_SSL (порт 465).
    """
    link = f"{VERIFY_URL_BASE}/{token}"

    def _send_sync():
        msg = EmailMessage()
        msg["Subject"] = "Подтверждение доступа к Telegram-боту"
        msg["From"] = EMAIL_FROM or EMAIL_HOST_USER
        msg["To"] = email
        msg.set_content(
            f"Здравствуйте!\n\nЧтобы подтвердить доступ к боту, перейдите по ссылке:\n"
            f"{link}\n\nСсылка действует {TOKEN_TTL_MINUTES} минут."
        )

        if EMAIL_USE_TLS and EMAIL_PORT == 587:
            # plain SMTP + STARTTLS
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
                smtp.send_message(msg)
        else:
            # SSL-wrapped SMTP (обычно порт 465)
            with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT) as smtp:
                smtp.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
                smtp.send_message(msg)

    # выполняем блок отправки в потоке
    await asyncio.to_thread(_send_sync)


async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, email, added_at, verified FROM telegram_users WHERE token = ?",
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

    # связка Telegram ↔ предприятие
    enterprise_number = await get_enterprise_number_by_bot_token(TELEGRAM_BOT_TOKEN)
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
