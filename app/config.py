import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings  # Pydantic v2 совместимо

# Загружаем переменные из .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# ───────── Pydantic-класс для настроек ─────────
class Settings(BaseSettings):
    DB_PATH: str = os.environ.get("DB_PATH") or str(Path(__file__).parent.parent / "asterisk_events.db")
    ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "SuperS3cret!")

    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID")

    NOTIFY_BOT_TOKEN: str = os.environ.get("NOTIFY_BOT_TOKEN")
    NOTIFY_CHAT_ID: str = os.environ.get("NOTIFY_CHAT_ID")

    EMAIL_HOST: str = os.environ.get("EMAIL_HOST")
    EMAIL_PORT: int = int(os.environ.get("EMAIL_PORT", 587))
    EMAIL_HOST_USER: str = os.environ.get("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD: str = os.environ.get("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS: bool = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
    EMAIL_FROM: str = os.environ.get("EMAIL_FROM", os.environ.get("EMAIL_HOST_USER"))
    VERIFY_URL_BASE: str = os.environ.get("VERIFY_URL_BASE")

    class Config:
        env_file = ".env"


# ───────── Создаём глобальный объект настроек ─────────
settings = Settings()

# ───────── Проверка обязательных значений ─────────
required_vars = [
    settings.TELEGRAM_BOT_TOKEN,
    settings.TELEGRAM_CHAT_ID,
    settings.NOTIFY_BOT_TOKEN,
    settings.NOTIFY_CHAT_ID,
    settings.EMAIL_HOST,
    settings.EMAIL_HOST_USER,
    settings.EMAIL_HOST_PASSWORD,
    settings.VERIFY_URL_BASE,
]

if not all(required_vars):
    raise RuntimeError("Одна или несколько обязательных переменных не заданы в .env")
