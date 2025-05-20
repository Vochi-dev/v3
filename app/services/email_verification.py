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
# Создаём/проверяем таблицу email_tokens с полями email, tg_id, bot_token, token, created_at
# ────────────────────────────────────────────────────────────────────────────────
_conn = sqlite3.connect(settings.DB_PATH)
_cur = _conn.cursor()
_cur.execute("""
    CREATE TABLE IF NOT EXISTS email_tokens (
        email       TEXT    PRIMARY KEY,
        tg_id       INTEGER NOT NULL,
        bot_token   TEXT    NOT NULL,
        token       TEXT    NOT NULL,
        created_at  TEXT    NOT NULL
    )
""")
_conn.commit()
_conn.close()


def create_and_store_token(email: str, tg_id: int, bot_token: str) -> str:
    """
    Генерирует токен, сохраняет его в email_tokens вместе с tg_id и bot_token,
    возвращает токен.
    """
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.utcnow().isoformat()
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO email_tokens
            (email, tg_id, bot_token, token, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (email, tg_id, bot_token, token, created_at)
    )
    conn.commit()
    conn.close()
    return token


def delete_token(token: str):
    """
    Удаляет запись токена из email_tokens.
    """
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM email_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def send_verification_email(email: str, token: str):
    """
    Отправляет письмо с ссылкой для подтверждения email.
    """
    link = f"{settings.VERIFY_URL_BASE}?token={token}"
    subject = "Подтверждение email"
    body = f"""Здравствуйте!

Чтобы подтвердить ваш email, пожалуйста перейдите по этой ссылке:

{link}

Если вы не запрашивали доступ — проигнорируйте это письмо.
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
# Асинхронные функции для работы с telegram_users и подтверждения токена
# ────────────────────────────────────────────────────────────────────────────────

async def email_exists(email: str) -> bool:
    """
    Проверяет наличие email в таблице email_users.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM email_users WHERE email = ?", (email,))
        return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    """
    Проверяет, не помечён ли уже email как verified в telegram_users.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT verified FROM telegram_users WHERE email = ?", (email,))
        row = await cur.fetchone()
        return bool(row and row[0] == 1)


async def upsert_telegram_user(tg_id: int, email: str, token: str, bot_token: str):
    """
    Вставляет или обновляет запись в telegram_users по email, 
    очищая прошлую запись для этого tg_id (если email другой).
    Ставит verified = 0 до клика по ссылке.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        # удаляем старую запись с этим tg_id (если email отличается)
        await db.execute(
            "DELETE FROM telegram_users WHERE tg_id = ? AND email != ?",
            (tg_id, email)
        )
        # вставляем или обновляем по email
        await db.execute(
            """
            INSERT INTO telegram_users (tg_id, email, token, verified, bot_token)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(email) DO UPDATE SET
                tg_id     = excluded.tg_id,
                token     = excluded.token,
                verified  = 0,
                bot_token = excluded.bot_token
            """,
            (tg_id, email, token, bot_token)
        )
        await db.commit()


async def mark_verified(token: str) -> tuple[bool, int | None]:
    """
    Проверяет токен в email_tokens, помечает telegram_users.verified = 1 и 
    возвращает (True, tg_id). Если токен не найден или устарел — (False, None).
    """
    # 1) получаем запись из email_tokens
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT email, tg_id, bot_token, created_at FROM email_tokens WHERE token = ?",
        (token,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, None

    email, tg_id, bot_token, created_at_str = row
    created_at = datetime.datetime.fromisoformat(created_at_str)
    if datetime.datetime.utcnow() - created_at > datetime.timedelta(hours=24):
        # устарел
        delete_token(token)
        return False, None

    # 2) отмечаем verified = 1 в telegram_users
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "UPDATE telegram_users SET verified = 1 WHERE email = ?",
            (email,)
        )
        await db.commit()

    # 3) удаляем использованный токен
    delete_token(token)

    return True, tg_id
