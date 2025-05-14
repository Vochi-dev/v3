# app/services/email_verification.py
# -*- coding: utf-8 -*-
"""
Подтверждение e-mail c учётом схемы:
  • email_users.number → enterprises.number (TEXT PK)
  • telegram_users не хранит enterprise, только email/token/verified
"""

from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import smtplib
from email.message import EmailMessage
from pathlib import Path
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

# ------------------------------------------------------------------ #
TOKEN_TTL_MINUTES = 30


def random_token(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


# ---------- проверки ------------------------------------------------
async def email_exists_for_enterprise(email: str, enterprise_number: str) -> bool:
    """
    Есть ли email в email_users и принадлежит ли нужному enterprise.number
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT 1
              FROM email_users
             WHERE email  = ?
               AND number = ?
            """,
            (email, enterprise_number),
        ) as cur:
            return (await cur.fetchone()) is not None


async def email_already_linked(email: str) -> bool:
    """
    True, если email уже в telegram_users и verified = 1
    (т.е. активирован в каком-то боте)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM telegram_users WHERE email = ? AND verified = 1",
            (email,),
        ) as cur:
            return (await cur.fetchone()) is not None


# ---------- вставка / обновление -----------------------------------
async def upsert_telegram_user(tg_id: int, email: str, token: str) -> None:
    """
    Сохраняем (tg_id, email, token, verified=0). Если email уже есть,
    перезаписываем tg_id и token, сбрасываем verified.
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


# ---------- письмо --------------------------------------------------
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
            f"Ссылка активна {TOKEN_TTL_MINUTES} минут."
        )
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)

    await asyncio.to_thread(_sync_send)


# ---------- подтверждение токена ------------------------------------
async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    """
    Если токен найден и не просрочен — ставим verified=1, очищаем token,
    возвращаем (True, tg_id). Иначе (False, None).
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

        # TTL по столбцу added_at ("YYYY-MM-DD HH:MM:SS")
        added_at = dt.datetime.strptime(row["added_at"], "%Y-%m-%d %H:%M:%S")
        if dt.datetime.utcnow() - added_at > dt.timedelta(minutes=TOKEN_TTL_MINUTES):
            return False, None

        await db.execute(
            """
            UPDATE telegram_users
               SET verified = 1,
                   token    = NULL
             WHERE token = ?
            """,
            (token,),
        )
        await db.commit()

    return True, row["tg_id"]
