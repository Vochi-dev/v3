# app/services/email_verification.py
# -*- coding: utf-8 -*-

import smtplib
import sqlite3
import secrets
import datetime
from email.message import EmailMessage

import aiosqlite

from app.config import settings

# ────────────────────────────────────────────────────────────────────────────────
# При первом импорте модуля гарантируем, что таблица email_tokens существует
# ────────────────────────────────────────────────────────────────────────────────
_conn = sqlite3.connect(settings.DB_PATH)
_cur = _conn.cursor()
_cur.execute("""
    CREATE TABLE IF NOT EXISTS email_tokens (
        email TEXT PRIMARY KEY,
        token TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
""")
_conn.commit()
_conn.close()


def create_verification_token(email: str) -> str:
    """
    Генерирует токен, сохраняет его в БД вместе с timestamp и возвращает его.
    """
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.utcnow().isoformat()
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO email_tokens (email, token, created_at) VALUES (?, ?, ?)",
        (email, token, created_at)
    )
    conn.commit()
    conn.close()
    return token


def get_email_by_token(token: str) -> str | None:
    """
    Возвращает email по токену, если он существует.
    """
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT email FROM email_tokens WHERE token = ?",
        (token,)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def delete_token(token: str):
    """
    Удаляет использованный токен из таблицы.
    """
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM email_tokens WHERE token = ?",
        (token,)
    )
    conn.commit()
    conn.close()


def send_verification_email(email: str, token: str):
    """
    Отправляет письмо с ссылкой для подтверждения.
    """
    link = f"{settings.VERIFY_URL_BASE}?token={token}"
    subject = "Подтверждение email"
    body = f"""Здравствуйте!

Чтобы подтвердить ваш email, пожалуйста перейдите по ссылке:

{link}

Если вы не запрашивали доступ — просто проигнорируйте это письмо.
"""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT) as smtp:
            smtp.ehlo()
            if settings.EMAIL_USE_TLS:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        raise RuntimeError(f"Ошибка отправки email: {e}")


# ────────────────────────────────────────────────────────────────────────────────
# Асинхронные функции для проверки и обновления telegram_users
# ────────────────────────────────────────────────────────────────────────────────

async def email_exists(email: str) -> bool:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT 1 FROM email_users WHERE email = ?", (email,)) as cursor:
            return await cursor.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT verified FROM telegram_users WHERE email = ?", (email,)) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0] == 1)


async def upsert_telegram_user(user_id: int, email: str, token: str, bot_token: str):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO telegram_users (user_id, email, token, verified, bot_token)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(email) DO UPDATE SET
                user_id   = excluded.user_id,
                token     = excluded.token,
                verified  = 0,
                bot_token = excluded.bot_token
            """,
            (user_id, email, token, bot_token)
        )
        await db.commit()
