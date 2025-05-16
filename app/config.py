# app/config.py
import os

# Путь к файлу базы данных
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "asterisk_events.db")

# Админ-пароль для /admin (берётся из окружения или берём дефолт)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "your_default_admin_password")

# Telegram-токен (опциональный, проверим при старте бота)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# (Если у вас где-то использовался TELEGRAM_CHAT_ID, аналогично:)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
