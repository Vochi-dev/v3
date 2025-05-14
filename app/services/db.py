>/dev/null <<'PY'
import sqlite3
from app.config import DB_PATH

def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_all(sql: str, params: tuple = ()):
    with connect() as conn:
        return conn.execute(sql, params).fetchall()

def execute(sql: str, params: tuple = ()):
    with connect() as conn:
        conn.execute(sql, params)
        conn.commit()