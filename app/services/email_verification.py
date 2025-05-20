# app/services/email_verification.py
# -*- coding: utf-8 -*-

import smtplib
import sqlite3
import secrets
import datetime
from email.message import EmailMessage

import aiosqlite
from app.config import settings

# — создаём/проверяем таблицу email_tokens с новыми колонками —
_conn = sqlite3.connect(settings.DB_PATH)
_cur = _conn.cursor()
_cur.execute("""
    CREATE TABLE IF NOT EXISTS email_tokens (
        email       TEXT PRIMARY KEY,
        tg_id       INTEGER NOT NULL,
        bot_token   TEXT    NOT NULL,
        token       TEXT    NOT NULL,
        created_at  TEXT    NOT NULL
    )
""")
_conn.commit()
_conn.close()


def create_and_store_token(email: str, tg_id: int, bot_token: str) -> str:
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.utcnow().isoformat()
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO email_tokens (email, tg_id, bot_token, token, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, tg_id, bot_token, token, created_at)
    )
    conn.commit()
    conn.close()
    return token


def delete_token(token: str):
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM email_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def send_verification_email(email: str, token: str):
    link = f"{settings.VERIFY_URL_BASE}?token={token}"
    subject = "Подтверждение email"
    body = f"""Здравствуйте!

Чтобы подтвердить ваш email, пожалуйста перейдите по этой ссылке:

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


async def email_exists(email: str) -> bool:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM email_users WHERE email = ?", (email,))
        return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT verified FROM telegram_users WHERE email = ?", (email,))
        row = await cur.fetchone()
        return bool(row and row[0] == 1)


async def upsert_telegram_user(tg_id: int, email: str, token: str, bot_token: str):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        # удалим старую запись с этим tg_id (если другой email)
        await db.execute("DELETE FROM telegram_users WHERE tg_id = ? AND email != ?", (tg_id, email))
        # вставим или обновим по email
        await db.execute("""
            INSERT INTO telegram_users (tg_id, email, token, verified, bot_token)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(email) DO UPDATE SET
                tg_id    = excluded.tg_id,
                token    = excluded.token,
                verified = 1,
                bot_token = excluded.bot_token
        """, (tg_id, email, token, bot_token))
        await db.commit()


async def verify_token_and_register_user(token: str):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute("SELECT email, tg_id, bot_token, created_at FROM email_tokens WHERE token = ?", (token,)).fetchone()
        if not row:
            raise RuntimeError("Токен не найден")
        created = datetime.datetime.fromisoformat(row["created_at"])
        if datetime.datetime.utcnow() - created > datetime.timedelta(hours=24):
            raise RuntimeError("Токен устарел")

        # регистрируем пользователя в telegram_users (verified=1)
        await upsert_telegram_user(row["tg_id"], row["email"], token, row["bot_token"])
        # удаляем токен
        await db.execute("DELETE FROM email_tokens WHERE token = ?", (token,))
        await db.commit()
