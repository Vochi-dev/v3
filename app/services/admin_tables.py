# app/services/admin_tables.py
import sqlite3
from app.config import DB_PATH

def fetch_all(sql: str, params: tuple = ()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()

# ───────── Списки для админ-панели ─────────
def get_enterprises():
    return fetch_all("SELECT * FROM enterprises ORDER BY number")

def get_requests():
    return fetch_all("SELECT * FROM user_requests ORDER BY created_at DESC")
