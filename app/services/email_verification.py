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
# Создаём таблицу email_tokens, если её нет
# ────────────────────────────────────────────────────────────────────────────────
_conn = sqlite3.connect(settings.DB_PATH)
_cur = _conn.cursor()
_cur.execute("""
    CREATE TABLE IF NOT EXISTS email_tokens (
        email       TEXT PRIMARY KEY,
        token       TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
""")
_conn.commit()
_conn.close()


def create_verification_token(email: str) -> str:
    """
    Генерирует токен, сохраняет его в БД вместе с меткой времени и возвращает.
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
    Возвращает email по токену, или None если не найден.
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
    Удаляет использованный токен.
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
# Асинхронные функции для работы с telegram_users и проверки/подтверждения токена
# ────────────────────────────────────────────────────────────────────────────────

async def email_exists(email: str) -> bool:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT 1 FROM email_users WHERE email = ?", (email,)) as cur:
            return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        async with db.execute("SELECT verified FROM telegram_users WHERE email = ?", (email,)) as cur:
            row = await cur.fetchone()
            return bool(row and row[0] == 1)


async def upsert_telegram_user(tg_id: int, email: str, token: str, bot_token: str):
    """
    Вставляет или обновляет запись в telegram_users по первичному ключу tg_id.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO telegram_users (tg_id, email, token, verified, bot_token)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                email     = excluded.email,
                token     = excluded.token,
                verified  = 0,
                bot_token = excluded.bot_token
            """,
            (tg_id, email, token, bot_token)
        )
        await db.commit()


async def mark_verified(token: str) -> tuple[bool, int | None]:
    """
    Проверяет токен, помечает telegram_users.verified = 1 и возвращает (True, tg_id).
    Если токен не найден — (False, None).
    """
    # 1) найдём email по токену
    email = get_email_by_token(token)
    if not email:
        return False, None

    # 2) обновим запись telegram_users
    async with aiosqlite.connect(settings.DB_PATH) as db:
        # извлечём tg_id
        async with db.execute(
            "SELECT tg_id FROM telegram_users WHERE email = ?",
            (email,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False, None
        tg_id = row[0]
        # установим verified = 1
        await db.execute(
            "UPDATE telegram_users SET verified = 1 WHERE tg_id = ?",
            (tg_id,)
        )
        await db.commit()

    # 3) удалим токен
    delete_token(token)

    return True, tg_id
