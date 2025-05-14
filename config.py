# app/config.py
# -*- coding: utf-8 -*-

import os
from dotenv import load_dotenv

load_dotenv()

# ───────── Telegram Bot credentials ───────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
)
TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "374573193"
)

# Админский чат для уведомлений (по умолчанию — тот же, что и TELEGRAM_CHAT_ID)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", TELEGRAM_CHAT_ID)

# ───────── Путь к базе событий ────────────────────────────────────────
DB_PATH = os.getenv(
    "DB_PATH",
    "/root/asterisk-webhook/asterisk_events.db"
)

# ───────── SMTP / e-mail настройки ────────────────────────────────────
EMAIL_HOST          = os.getenv("EMAIL_HOST", "mailbe04.hoster.by")
EMAIL_PORT          = int(os.getenv("EMAIL_PORT", 465))
EMAIL_HOST_USER     = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS       = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_FROM          = os.getenv("EMAIL_FROM", EMAIL_HOST_USER)

# То же, что EMAIL_*, но под именами, которые ждёт модуль верификации
SMTP_HOST = EMAIL_HOST
SMTP_PORT = EMAIL_PORT
SMTP_USER = EMAIL_HOST_USER
SMTP_PASS = EMAIL_HOST_PASSWORD

# Базовый URL вашего FastAPI для ссылок вида
# https://<HOST>:<PORT>/auth_email/verify-email/<token>
VERIFY_URL_BASE = os.getenv(
    "VERIFY_URL_BASE",
    "https://10.88.10.110:8001/auth_email/verify-email"
)
