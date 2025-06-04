import asyncio
import sys
import os

# Добавляем текущую директорию в PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.init_postgres import init_database

if __name__ == "__main__":
    asyncio.run(init_database()) 