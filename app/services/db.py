# app/services/db.py
import sqlite3
from pathlib import Path

from app.config import DB_PATH   # путь к БД уже есть в config.py

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)   # на всякий случай

def get_connection() -> sqlite3.Connection:
    """
    Открывает БД и включает row_factory → dict-like доступ.
    Использовать так:

        conn = get_connection()
        rows = conn.execute("SELECT * FROM enterprises").fetchall()
        conn.close()
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
