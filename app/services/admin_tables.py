# app/services/admin_tables.py
import sqlite3
from typing import List, Dict
from app.config import DB_PATH

def fetch_all(q: str, params=()) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# предприятия
def get_enterprises() -> List[Dict]:
    return fetch_all("SELECT * FROM enterprises ORDER BY id")

# заявки
def get_requests() -> List[Dict]:
    sql = """
      SELECT ur.id,
             ur.email,
             e.name       AS enterprise,
             ur.status,
             ur.created_at
        FROM user_requests ur
   LEFT JOIN enterprises  e ON e.id = ur.enterprise_id
    ORDER BY ur.created_at DESC
    """
    return fetch_all(sql)
