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

from app.config import DB_PATH, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, VERIFY_URL_BASE

# Время жизни токена в минутах
TOKEN_TTL_MINUTES = 30


def random_token(nbytes: int = 16) -> str:
    """Генерирует URL-дружественный токен."""
    return secrets.token_urlsafe(nbytes)


async def email_exists(email: str) -> bool:
    """Проверяет, что e-mail есть в таблице email_users."""
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
    Добавляет или обновляет запись в telegram_users:
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
    Формирует ссылку VERIFY_URL_BASE/<token> и отправляет письмо.
    """
    link = f"{VERIFY_URL_BASE}/{token}"

    def _send_sync() -> None:
        msg = EmailMessage()
        msg["Subject"] = "Подтверждение доступа к Telegram-боту"
        msg["From"] = SMTP_USER
        msg["To"] = email
        msg.set_content(
            "Здравствуйте!\n\n"
            "Чтобы подтвердить доступ к боту, перейдите по ссылке:\n"
            f"{link}\n\n"
            f"Ссылка действительна {TOKEN_TTL_MINUTES} минут."
        )
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)

    await asyncio.to_thread(_send_sync)


async def mark_verified(token: str) -> Tuple[bool, Optional[int]]:
    """
    Подтверждает токен:
    • ищет запись в telegram_users по token
    • проверяет TTL по полю added_at
    • если всё ок — ставит verified=1 и обнуляет token
    Возвращает (True, tg_id) или (False, None).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, added_at, verified FROM telegram_users WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()

        # нет такой записи
        if row is None:
            return False, None

        # уже подтверждён ранее
        if row["verified"] == 1:
            return True, row["tg_id"]

        # парсим время добавления
        added_at_str = row["added_at"]
        try:
            # ожидаемый формат "YYYY-MM-DD HH:MM:SS"
            added_at = dt.datetime.strptime(added_at_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # fallback: ISO формат
            try:
                added_at = dt.datetime.fromisoformat(added_at_str)
            except Exception:
                return False, None

        # проверяем жизнь токена
        if dt.datetime.utcnow() - added_at > dt.timedelta(minutes=TOKEN_TTL_MINUTES):
            return False, None

        # подтверждаем
        await db.execute(
            "UPDATE telegram_users SET verified = 1, token = NULL WHERE token = ?",
            (token,),
        )
        await db.commit()

    return True, row["tg_id"]
