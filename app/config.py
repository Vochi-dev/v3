import os
from dotenv import load_dotenv
from pathlib import Path

# Загружаем .env из корня
load_dotenv(Path(__file__).parent.parent / ".env")

# Пароль для админки
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
