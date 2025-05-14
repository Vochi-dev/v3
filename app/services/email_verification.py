# app/services/email_verification.py
# -*- coding: utf-8 -*-
"""
Упрощённая логика:
• e-mail считается валидным, если есть в email_users (без привязки к enterprise)
• e-mail может быть активирован только в одном боте (telegram_users.verified = 1)
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

TOKEN_TTL_MINUTES = 30


# ---------- utils ----------------------------------------------------
def random_token(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


# ---------- проверки -------------------------------------------------
async def email_exists(email: str) -> bool:
    """Есть ли e-mail в таблице email_users (независимо от предприятия)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM email_users WHERE email = ?", (email,)
        ) as cur:
            return (await cur.fetchone()) is not None


async def email_already_verified(email: str) -> bool:
    """True, если e-mail уже verified в telegram_users (то есть занят)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM telegram_users WHERE email = ? AND verified = 1",
            (email,),
        ) as cur:
            return (await cur.fetchone()) is not None


# ---------- вставка / обновление ------------------------------------
async def upsert_telegram_user(tg_id: int, email: str, token: str) -> None:
    """
    Сохраняем (tg_id, email, token, verified=0).
    Если e-mail уже был, сбрасываем verified и обновляем token.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO telegram_users (tg_id, email, token, verified, added_at)
                 VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE
                  SET tg_id   = excluded.tg_id,
                      token   = excluded.token,
                      verified= 0,
                      added_at= CURRENT_TIMESTAMP
            """,
            (tg_id, email, token),
        )
        await db.commit()


# ---------- письмо ---------------------------------------------------
async def send_verification_email(email: str, token: str) -> None:
    link = f"{VERIFY_URL_BASE}/{token}"

    def _sync_send() -> None:
        msg = EmailMessage()
        msg["Subject"] = "Подтверждение доступа к Telegram-боту"
        msg["From"] = SMTP_USER
        msg["To"] = email
        msg.set_content(
            f"Здравствуйте!\n\n"
            f"Для подтверждения доступа перейдите по ссылке:\n{link}\n\n"
            f"Ссылка действительна {TOKEN_TTL_MINUTES} минут."
        )
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)

    await asyncio.to_thread(_sync_send)


# ---------- подтверждение токена -------------------------------------
async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    """
    Делает verified=1, если токен валиден и не просрочен.
    Возвращает (успех, tg_id | None).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, added_at, verified FROM telegram_users WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return False, None
        if row["verified"] == 1:
            return True, row["tg_id"]

        added_at = dt.datetime.strptime(row["added_at"], "%Y-%m-%d %H_]()_
