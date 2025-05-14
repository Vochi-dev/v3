import sqlite3, secrets, string, smtplib, ssl
from email.message import EmailMessage
from datetime import datetime
from app.config import (
    DB_PATH,
    EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
    EMAIL_USE_TLS, EMAIL_FROM,
)
from app.services.db import get_connection   # если у вас уже есть хелпер

TOKEN_LEN = 32

def _random_token(n: int = TOKEN_LEN) -> str:
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))

# ───────── БД ──────────────────────────────────────────────────────────
def upsert_telegram_user(tg_id: int, email: str, token: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO telegram_users (tg_id, email, token, verified, added_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(tg_id) DO UPDATE SET email=excluded.email, token=excluded.token, verified=0
        """, (tg_id, email, token, datetime.utcnow().isoformat()))
        conn.commit()

def mark_verified(token: str) -> tuple[bool, int]:
    """Вернуть (успех, tg_id)"""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT tg_id FROM telegram_users WHERE token=? AND verified=0", (token,))
        row = cur.fetchone()
        if not row:
            return False, 0
        tg_id = row["tg_id"]
        cur.execute("UPDATE telegram_users SET verified=1 WHERE tg_id=?", (tg_id,))
        conn.commit()
        return True, tg_id

def email_exists(email: str) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM email_users WHERE email=?", (email,))
        return cur.fetchone() is not None

def email_already_linked(email: str, bot_token: str) -> bool:
    """Не позволяем привязать e-mail к другому боту"""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.bot_token
            FROM telegram_users tu
            JOIN enterprises e ON e.number = substr(tu.email,1,4)  -- ваша логика связи, поправьте
            WHERE tu.email=? AND tu.verified=1
        """, (email,))
        row = cur.fetchone()
        return row and row["bot_token"] != bot_token

# ───────── Письмо ──────────────────────────────────────────────────────
def send_verification_email(email: str, token: str):
    link = f"https://bot.vochi.by:8001/verify-email/{token}"

    msg = EmailMessage()
    msg["Subject"] = "Подтверждение подключения к боту"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = email
    msg.set_content(
        f"Здравствуйте!\n\nНажмите на ссылку ниже, чтобы закончить подключение:\n{link}\n\n"
        "Если это письмо попало к вам случайно — просто игнорируйте его."
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as smtp:
        if EMAIL_USE_TLS:
            smtp.starttls(context=context)
        smtp.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
        smtp.send_message(msg)
