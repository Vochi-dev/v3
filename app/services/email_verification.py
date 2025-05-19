# app/services/email_verification.py

import smtplib
import sqlite3
import secrets
from email.message import EmailMessage

from app.config import settings


def create_verification_token(email: str) -> str:
    """Генерирует токен и сохраняет его в БД"""
    token = secrets.token_urlsafe(32)
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO email_tokens (email, token) VALUES (?, ?)",
        (email, token)
    )
    conn.commit()
    conn.close()
    return token


def get_email_by_token(token: str) -> str | None:
    """Возвращает email по токену, если он существует"""
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
    """Удаляет использованный токен"""
    conn = sqlite3.connect(settings.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM email_tokens WHERE token = ?",
        (token,)
    )
    conn.commit()
    conn.close()


def send_verification_email(email: str, token: str):
    """Отправляет письмо с ссылкой для подтверждения"""
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
