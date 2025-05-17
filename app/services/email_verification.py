# -*- coding: utf-8 -*-
"""
Надёжная логика подтверждения e-mail:
• random_token
• проверка email
• upsert_telegram_user сохраняет токен и bot_token
• send_verification_email отправляет письмо через STARTTLS
• mark_verified подтв. токена и запись в enterprise_users
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
    EMAIL_HOST, EMAIL_PORT,
    EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
    EMAIL_FROM, VERIFY_URL_BASE,
)
from app.services.db import get_enterprise_number_by_bot_token

TOKEN_TTL_MINUTES = 30


def random_token(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


async def email_exists(email: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM email_users WHERE email = ?", (email,)) as cur:
            return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM telegram_users WHERE email = ? AND verified = 1", (email,)
        ) as cur:
            return await cur.fetchone() is not None


async def upsert_telegram_user(tg_id: int, email: str, token: str, bot_token: str) -> None:
    """
    Вставка или обновление данных о пользователе в базе данных.
    Если пользователь с таким tg_id существует, обновляем данные,
    иначе вставляем новую запись.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверка на существующую запись по tg_id
        async with db.execute("SELECT 1 FROM telegram_users WHERE tg_id = ?", (tg_id,)) as cur:
            existing_user = await cur.fetchone()

        if existing_user:
            # Если запись существует, обновляем данные
            await db.execute(
                """
                UPDATE telegram_users
                SET email = ?, token = ?, verified = 0, added_at = CURRENT_TIMESTAMP, bot_token = ?
                WHERE tg_id = ?
                """,
                (email, token, bot_token, tg_id),
            )
        else:
            # Если записи нет, вставляем новую
            await db.execute(
                """
                INSERT INTO telegram_users (tg_id, email, token, verified, added_at, bot_token)
                VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP, ?)
                """,
                (tg_id, email, token, bot_token),
            )

        await db.commit()


async def send_verification_email(email: str, token: str) -> None:
    link = f"{VERIFY_URL_BASE}/{token}"

    def _send():
        msg = EmailMessage()
        msg["Subject"] = "Подтверждение доступа к Telegram-боту"
        msg["From"] = EMAIL_FROM or EMAIL_HOST_USER
        msg["To"] = email
        msg.set_content(
            f"Здравствуйте!\n\n"
            f"Перейдите по ссылке, чтобы подтвердить доступ к боту:\n"
            f"{link}\n\n"
            f"Ссылка действительна {TOKEN_TTL_MINUTES} минут."
        )
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
            smtp.send_message(msg)

    await asyncio.to_thread(_send)


async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, added_at, verified, bot_token FROM telegram_users WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return False, None
        if row["verified"] == 1:
            return True, row["tg_id"]

        # парсим added_at (без миллисекунд или ISO)
        added_at_str = row["added_at"]
        try:
            added_at = dt.datetime.strptime(added_at_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                added_at = dt.datetime.fromisoformat(added_at_str)
            except Exception:
                return False, None

        if dt.datetime.utcnow() - added_at > dt.timedelta(minutes=TOKEN_TTL_MINUTES):
            return False, None

        await db.execute(
            "UPDATE telegram_users SET verified = 1, token = NULL WHERE token = ?",
            (token,),
        )
        await db.commit()

    # привязка к enterprise
    enterprise_number = await get_enterprise_number_by_bot_token(row["bot_token"])
    if enterprise_number:
        async with aiosqlite.connect(DB_PATH) as db2:
            await db2.execute(
                """
                INSERT INTO enterprise_users
                  (enterprise_id, telegram_id, username, requested_at, status)
                VALUES (?, ?, NULL, CURRENT_TIMESTAMP, 'approved')
                """,
                (enterprise_number, row["tg_id"]),
            )
            await db2.commit()

    return True, row["tg_id"]
