# app/config.py
# -*- coding: utf-8 -*-
"""
Единые настройки проекта. Добавлены SMTP_* и VERIFY_URL_BASE,
которые требуются модулю email_verification.py.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# 🛡️ Пароль администратора (для /admin/login)
ADMIN_PASSWORD = "SuperS3cret!"

# ───────── SMTP / e-mail ─────────────────────────────────────────────
EMAIL_HOST         = os.getenv("EMAIL_HOST", "mailbe04.hoster.by")
EMAIL_PORT         = int(os.getenv("EMAIL_PORT", 587))
EMAIL_HOST_USER    = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD= os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS      = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_FROM         = os.getenv("EMAIL_FROM", EMAIL_HOST_USER)

# ↓↓↓ переменные, которые импортирует email_verification.py ↓↓↓
SMTP_HOST = EMAIL_HOST
SMTP_PORT = EMAIL_PORT
SMTP_USER = EMAIL_HOST_USER
SMTP_PASS = EMAIL_HOST_PASSWORD

# Базовый URL, куда приходит ссылка вида /auth_email/verify-email/<token>
# Замените на реальный адрес вашего FastAPI-сервера.
VERIFY_URL_BASE = os.getenv(
    "VERIFY_URL_BASE",
    "https://your-api.example.com/auth_email/verify-email",
)

# ───────── База данных ───────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/root/asterisk-webhook/asterisk_events.db")

# ───────── Telegram ─────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
