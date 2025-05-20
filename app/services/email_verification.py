# app/services/email_verification.py
# -*- coding: utf-8 -*-

import sqlite3
import secrets
import datetime
import smtplib
from email.message import EmailMessage

import aiosqlite

from app.config import settings

# ────────────────────────────────────────────────────────────────────────────────
# Инициализация таблицы email_tokens
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
# Обеспечим уникальность email в telegram_users
_cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ix_telegram_users_email
      ON telegram_users(email)
""")
_conn.commit()
_conn.close()


def create_and_store_token(email: str, tg_id: int, bot_token: str) -> str:
    """
    Генерирует токен, сохраняет его вместе с tg_id и bot_token и возвращает.
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
    Удаляет запись токена.
    """
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM email_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def send_verification_email(email: str, token: str):
    """
    Отправляет письмо с ссылкой для подтверждения.
    """
    link = f"{settings.VERIFY_URL_BASE}?token={token}"
    subject = "Подтверждение e-mail"
    body = f"""Здравствуйте!

Чтобы подтвердить ваш e-mail, перейдите по ссылке:

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
# Асинхронные функции для telegram_users и проверки токена
# ────────────────────────────────────────────────────────────────────────────────

async def email_exists(email: str) -> bool:
    """
    Проверяет, есть ли email в таблице email_users.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM email_users WHERE email = ?",
            (email,)
        )
        return await cur.fetchone() is not None


async def email_already_verified(email: str) -> bool:
    """
    Проверяет, помечён ли email в telegram_users.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT verified FROM telegram_users WHERE email = ?",
            (email,)
        )
        row = await cur.fetchone()
        return bool(row and row[0] == 1)


async def upsert_telegram_user(tg_id: int, email: str, token: str, bot_token: str):
    """
    Вставляет или обновляет запись в telegram_users после верификации.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        # 1) Удаляем старую строку с этим tg_id, если email другой
        await db.execute(
            "DELETE FROM telegram_users WHERE tg_id = ? AND email != ?",
            (tg_id, email)
        )
        # 2) Upsert по email
        await db.execute(
            """
            INSERT INTO telegram_users (tg_id, email, token, verified, bot_token)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(email) DO UPDATE SET
                tg_id     = excluded.tg_id,
                token     = excluded.token,
                verified  = 1,
                bot_token = excluded.bot_token
            """,
            (tg_id, email, token, bot_token)
        )
        await db.commit()


async def verify_token_and_register_user(token: str) -> None:
    """
    Проверяет токен, если он валиден и не старше 24 часов — регистрирует
    пользователя в telegram_users (verified=1) и удаляет токен.
    """
    # 1) читаем токен
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute(
            "SELECT email, tg_id, bot_token, created_at "
            "FROM email_tokens WHERE token = ?",
            (token,)
        ).fetchone()

    if not row:
        raise RuntimeError("Токен не найден или неверен.")

    # 2) проверяем время жизни (24 часа)
    created = datetime.datetime.fromisoformat(row["created_at"])
    if datetime.datetime.utcnow() - created > datetime.timedelta(hours=24):
        raise RuntimeError("Токен устарел. Запросите письмо повторно.")

    # 3) регистрируем в telegram_users
    await upsert_telegram_user(
        row["tg_id"],
        row["email"],
        token,
        row["bot_token"]
    )

    # 4) удаляем запись токена
    delete_token(token)
