# app/config.py
import os

# Путь к файлу базы данных
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "asterisk_events.db")

# Админ-пароль для /admin
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "your_default_admin_password")

# Telegram-токен берём из переменной окружения
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Переменная окружения TELEGRAM_BOT_TOKEN не задана")

# Другие настройки, если нужны:
# e.g. SMTP_HOST, SMTP_PORT, EMAIL_FROM и т.п., тоже обычно из os.environ
