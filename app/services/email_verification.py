# app/services/email_verification.py
# -*- coding: utf-8 -*-
"""
Упрощённая логика подтверждения e-mail:
• генерация токена
• проверка наличия e-mail в email_users
• проверка, что e-mail не подтверждён ранее
• upsert в telegram_users
• отправка письма с ссылкой
• mark_verified — подтверждение токена
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
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASS,
    VERIFY_URL_BASE,
)

TOKEN_TTL_MINUTES = 30


def random_token(nbytes: int = 16) -> str:
    """URL-friendly токен."""
    return secrets.token_urlsafe(nbytes)


async def email_exists(email: str) -> bool:
    """Проверяем, что e-mail есть в таблице email_users."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM email_users WHERE email = ?",
            (email,),
        ) as cur:
            return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    """True, если e-mail уже подтверждён (verified = 1) в telegram_users."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM telegram_users WHERE email = ? AND verified = 1",
            (email,),
        ) as cur:
            return await cur.fetchone() is not None


async def upsert_telegram_user(tg_id: int, email: str, token: str) -> None:
    """
    Добавляем или обновляем запись в telegram_users:
    • tg_id, email, token, verified=0, added_at=CURRENT_TIMESTAMP
    """
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
    Формируем ссыл
