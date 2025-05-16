# app/config.py
import os

# При желании можно раскомментировать и использовать python-dotenv:
# from dotenv import load_dotenv
# load_dotenv()

# ───────── Путь к базе ─────────
# Если в окружении задан, возьмём его, иначе по умолчанию под проектом
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "asterisk_events.db")
)

# ───────── Админ-пароль ─────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SuperS3cret!")

# ───────── Корпоративный бот ─────────
# Этот токен используется для webhook-логики (events → Telegram)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в окружении")

# Полезно, если где-то нужен единый chat_id (редко)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ───────── Notify-бот для админ-уведомлений ─────────
# Если у вас есть отдельный токен – установите NOTIFY_BOT_TOKEN и NOTIFY_CHAT_ID,
# иначе по умолчанию будет использоваться корпоративный бот.
NOTIFY_BOT_TOKEN = os.environ.get("NOTIFY_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
NOTIFY_CHAT_ID  = os.environ.get("NOTIFY_CHAT_ID", TELEGRAM_CHAT_ID)
if NOTIFY_BOT_TOKEN and not NOTIFY_CHAT_ID:
    raise RuntimeError("Установите NOTIFY_CHAT_ID для вашего notify-бота")

# ───────── Email-верификация ─────────
EMAIL_HOST         = os.environ["EMAIL_HOST"]
EMAIL_PORT         = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER    = os.environ["EMAIL_HOST_USER"]
EMAIL_HOST_PASSWORD= os.environ["EMAIL_HOST_PASSWORD"]
EMAIL_USE_TLS      = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
EMAIL_FROM         = os.environ.get("EMAIL_FROM", EMAIL_HOST_USER)
VERIFY_URL_BASE    = os.environ.get("VERIFY_URL_BASE", "http://localhost:8001/verify-email")

# ───────── Доп. настройки ─────────
# (Добавляйте сюда другие переменные из вашего .env по необходимости)
