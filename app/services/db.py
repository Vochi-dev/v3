import sqlite3
import aiosqlite
from config import DB_PATH

def init_database_tables():
    """
    Create the required tables in the SQLite database.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Создаем таблицу email_users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            right_all BOOLEAN DEFAULT 0,
            right_1 BOOLEAN DEFAULT 0,
            right_2 BOOLEAN DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()

async def get_connection():
    """
    Функция для получения асинхронного подключения к базе данных SQLite.
    """
    return await aiosqlite.connect(DB_PATH)
