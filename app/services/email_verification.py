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
# Добавим уникальный индекс для email в telegram_users (если нет)
_cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ix_telegram_users_email ON telegram_users(email)
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
        with smtplib.SM
