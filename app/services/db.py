import sqlite3
import aiosqlite
from app.config import DB_PATH


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


async def get_enterprise_number_by_bot_token(bot_token: str) -> str:
    """
    Получение номера предприятия по bot_token.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT number FROM enterprises WHERE bot_token = ?", (bot_token,)
        ) as cur:
            row = await cur.fetchone()

    return row["number"] if row else None


def get_enterprise_name_by_number(enterprise_number: str) -> str | None:
    """
    Получение названия предприятия по номеру.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM enterprises WHERE number = ?", (enterprise_number,)
    )
    row = cur.fetchone()
    conn.close()

    return row[0] if row else None
