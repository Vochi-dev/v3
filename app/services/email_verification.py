# app/services/email_verification.py
# -*- coding: utf-8 -*-

import secrets
import datetime
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.services.postgres import get_pool

# ────────────────────────────────────────────────────────────────────────────────
# Инициализация таблиц в PostgreSQL (выполняется автоматически через миграции)
# ────────────────────────────────────────────────────────────────────────────────

async def create_and_store_token(email: str, tg_id: int, bot_token: str) -> str:
    """
    Генерирует токен, сохраняет его вместе с tg_id и bot_token и возвращает.
    """
    token = secrets.token_urlsafe(32)
    created_at = datetime.datetime.utcnow()
    
    pool = await get_pool()
    if not pool:
        raise RuntimeError("Database pool not available")
        
    async with pool.acquire() as conn:
        # Создаем таблицу email_tokens если её нет
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_tokens (
                email       TEXT    PRIMARY KEY,
                tg_id       INTEGER NOT NULL,
                bot_token   TEXT    NOT NULL,
                token       TEXT    NOT NULL,
                created_at  TIMESTAMP NOT NULL
            )
        """)
        
        await conn.execute(
            """
            INSERT INTO email_tokens 
                (email, tg_id, bot_token, token, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (email) DO UPDATE SET
                tg_id = EXCLUDED.tg_id,
                bot_token = EXCLUDED.bot_token,
                token = EXCLUDED.token,
                created_at = EXCLUDED.created_at
            """,
            email, tg_id, bot_token, token, created_at
        )
    return token


async def delete_token(token: str):
    """
    Удаляет запись токена.
    """
    pool = await get_pool()
    if not pool:
        return
        
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM email_tokens WHERE token = $1", token)


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
# Асинхронные функции
# ────────────────────────────────────────────────────────────────────────────────

async def email_exists(email: str) -> bool:
    """
    Проверяет, есть ли email в списке разрешенных пользователей.
    """
    # Пока всегда возвращаем True, так как ограничения по email убрали
    return True


async def email_already_verified(email: str) -> bool:
    """
    Проверяет, есть ли email в telegram_users.
    """
    pool = await get_pool()
    if not pool:
        return False
        
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM telegram_users WHERE email = $1", email
        )
        return row is not None


async def upsert_telegram_user(tg_id: int, email: str, token: str, bot_token: str):
    """
    Вставляет или обновляет запись в telegram_users после верификации.
    """
    pool = await get_pool()
    if not pool:
        raise RuntimeError("Database pool not available")
        
    async with pool.acquire() as conn:
        # Удаляем старые записи этого tg_id с другими email
        await conn.execute(
            "DELETE FROM telegram_users WHERE tg_id = $1 AND email != $2",
            tg_id, email
        )
        
        # Вставляем или обновляем запись
        await conn.execute(
            """
            INSERT INTO telegram_users (tg_id, email, bot_token)
            VALUES ($1, $2, $3)
            ON CONFLICT(email) DO UPDATE SET
                tg_id = EXCLUDED.tg_id,
                bot_token = EXCLUDED.bot_token
            """,
            tg_id, email, bot_token
        )


async def verify_token_and_register_user(token: str) -> None:
    """
    Проверяет токен, если он валиден и не старше 24 часов — регистрирует
    пользователя в telegram_users и удаляет токен.
    """
    pool = await get_pool()
    if not pool:
        raise RuntimeError("Database pool not available")
        
    # 1) читаем токен
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, tg_id, bot_token, created_at "
            "FROM email_tokens WHERE token = $1",
            token
        )

    if not row:
        raise RuntimeError("Токен не найден или неверен.")

    # 2) проверяем возраст
    created = row["created_at"]
    if datetime.datetime.utcnow() - created > datetime.timedelta(hours=24):
        raise RuntimeError("Токен устарел. Запросите письмо повторно.")

    # 3) регистрируем
    await upsert_telegram_user(
        row["tg_id"],
        row["email"],
        token,
        row["bot_token"]
    )

    # 4) удаляем запись
    await delete_token(token)
