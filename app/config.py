import os
from dotenv import load_dotenv

load_dotenv()

# 🛡️ Пароль администратора (для /admin/login)
ADMIN_PASSWORD = "SuperS3cret!"

# 📨 Почта
EMAIL_HOST = os.getenv("EMAIL_HOST", "mailbe04.hoster.by")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_FROM = os.getenv("EMAIL_FROM", "")

# 📦 Путь к базе данных
DB_PATH = os.getenv("DB_PATH", "/root/asterisk-webhook/asterisk_events.db")

# 🤖 Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
