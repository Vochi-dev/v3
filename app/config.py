# app/config.py
import os

# ───────── Путь к базе ─────────
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "asterisk_events.db")
)

# ───────── Админ-пароль ─────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SuperS3cret!")

# ───────── Корпоративный бот ─────────
# Этот токен используется только процессом бота, а не FastAPI:
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# ───────── Notify-бот для админ-уведомлений ─────────
NOTIFY_BOT_TOKEN = os.environ.get("NOTIFY_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
NOTIFY_CHAT_ID  = os.environ.get("NOTIFY_CHAT_ID", TELEGRAM_CHAT_ID)

# ───────── Email-верификация ─────────
EMAIL_HOST          = os.environ.get("EMAIL_HOST", "mailbe04.hoster.by")
EMAIL_PORT          = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER", "bot@vochi.by")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS       = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("1","true","yes")
EMAIL_FROM          = os.environ.get("EMAIL_FROM", EMAIL_HOST_USER)
VERIFY_URL_BASE     = os.environ.get("VERIFY_URL_BASE", "https://bot.vochi.by/verify-email")
