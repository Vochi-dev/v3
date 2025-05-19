import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseSettings

# ───────── Загрузка переменных из .env ─────────
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# ───────── Путь к базе ─────────
DB_PATH = os.environ.get("DB_PATH") or str(Path(__file__).parent.parent / "asterisk_events.db")

# ───────── Админ-пароль ─────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SuperS3cret!")

# ───────── Корпоративный бот ─────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Не заданы TELEGRAM_BOT_TOKEN и/или TELEGRAM_CHAT_ID в .env")

# ───────── Notify-бот для фоновых уведомлений ─────────
NOTIFY_BOT_TOKEN = os.environ.get("NOTIFY_BOT_TOKEN")
NOTIFY_CHAT_ID   = os.environ.get("NOTIFY_CHAT_ID")

if not NOTIFY_BOT_TOKEN or not NOTIFY_CHAT_ID:
    raise RuntimeError("Не заданы NOTIFY_BOT_TOKEN и/или NOTIFY_CHAT_ID в .env")

# ───────── Email-верификация ─────────
EMAIL_HOST          = os.environ.get("EMAIL_HOST")
EMAIL_PORT          = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS       = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
EMAIL_FROM          = os.environ.get("EMAIL_FROM", EMAIL_HOST_USER)
VERIFY_URL_BASE     = os.environ.get("VERIFY_URL_BASE")

if not all([EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, VERIFY_URL_BASE]):
    raise RuntimeError("Параметры SMTP/VERIFY_URL_BASE не настроены в .env")

# ───────── Объект settings для pydantic-валидации ─────────
class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    NOTIFY_BOT_TOKEN: str
    NOTIFY_CHAT_ID: str
    DB_PATH: str
    EMAIL_HOST: str
    EMAIL_PORT: int
    EMAIL_HOST_USER: str
    EMAIL_HOST_PASSWORD: str
    EMAIL_USE_TLS: bool
    EMAIL_FROM: str
    VERIFY_URL_BASE: str
    ADMIN_PASSWORD: str

    class Config:
        env_file = ".env"


settings = Settings()
