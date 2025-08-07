import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Загружаем переменные из .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# ───────── Для обратной совместимости ─────────
DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "asterisk_events.db")
DB_PATH = os.environ.get("DB_PATH") or DEFAULT_DB_PATH
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SuperS3cret!")

# PostgreSQL конфигурация
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "r/Yskqh/ZbZuvjb2b3ahfg==")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "postgres")

# ───────── Pydantic-класс для settings ─────────
class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    NOTIFY_BOT_TOKEN: str
    NOTIFY_CHAT_ID: str
    EMAIL_HOST: str
    EMAIL_PORT: int = 587
    EMAIL_HOST_USER: str
    EMAIL_HOST_PASSWORD: str
    EMAIL_USE_TLS: bool = True
    EMAIL_FROM: str | None = None
    VERIFY_URL_BASE: str
    DB_PATH: str = DB_PATH
    ADMIN_PASSWORD: str = ADMIN_PASSWORD
    
    # PostgreSQL настройки
    POSTGRES_HOST: str = POSTGRES_HOST
    POSTGRES_PORT: int = POSTGRES_PORT
    POSTGRES_USER: str = POSTGRES_USER
    POSTGRES_PASSWORD: str = POSTGRES_PASSWORD
    POSTGRES_DB: str = POSTGRES_DB

    class Config:
        env_file = ".env"

settings = Settings()

# Подставим EMAIL_FROM, если он не задан
if not settings.EMAIL_FROM:
    settings.EMAIL_FROM = settings.EMAIL_HOST_USER

# Проверка обязательных переменных
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

# Добавляем новые переменные
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://user:password@host:port/dbname")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "a_very_secret_key_that_should_be_changed")

# Конфигурация БД для asyncpg
DB_CONFIG = {
    "user": POSTGRES_USER,
    "password": POSTGRES_PASSWORD,
    "database": POSTGRES_DB,
    "host": POSTGRES_HOST,
    "port": POSTGRES_PORT,
}
