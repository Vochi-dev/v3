# app/config.py
import os

# ───────── Путь к базе ─────────
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "asterisk_events.db")
)

# ───────── Админ-пароль ─────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SuperS3cret!")

# ───────── Корпоративный бот ─────────
# Токен используется в каждом корпоративном боте-процессе (app.telegram.bot)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# ───────── Notify-бот для админ-уведомлений ─────────
# Ваш новый отдельный бот-уведомлятель:
NOTIFY_BOT_TOKEN = os.environ.get(
    "NOTIFY_BOT_TOKEN",
    "7741688843:AAHp9ujGdv__4_E7B2SVTNL_u1VpIa3XqCk"
)
NOTIFY_CHAT_ID = os.environ.get("NOTIFY_CHAT_ID", "391634117")
if not NOTIFY_BOT_TOKEN or not NOTIFY_CHAT_ID:
    raise RuntimeError("Для работы notify-бота нужно задать NOTIFY_BOT_TOKEN и NOTIFY_CHAT_ID")

# ───────── Email-верификация ─────────
EMAIL_HOST          = os.environ.get("EMAIL_HOST", "mailbe04.hoster.by")
EMAIL_PORT          = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER", "bot@vochi.by")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS       = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
EMAIL_FROM          = os.environ.get("EMAIL_FROM", EMAIL_HOST_USER)
VERIFY_URL_BASE     = os.environ.get("VERIFY_URL_BASE", "https://bot.vochi.by/verify-email")
