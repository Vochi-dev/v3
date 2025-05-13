from db import get_db_connection
from datetime import datetime

def list_enterprises():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM enterprises ORDER BY number").fetchall()
    conn.close()
    return rows

def get_enterprise(number: str):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM enterprises WHERE number=?", (number,)).fetchone()
    conn.close()
    return row

def create_enterprise(number: str, name: str, name2: str, bot_token: str, chat_id: str,
                      ip: str, secret: str, host: str):
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO enterprises
          (number, name, name2, bot_token, chat_id, ip, secret, host, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (number, name, name2, bot_token, chat_id, ip, secret, host, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def update_enterprise(number: str, name: str, name2: str, bot_token: str, chat_id: str,
                      ip: str, secret: str, host: str):
    conn = get_db_connection()
    conn.execute(
        """
        UPDATE enterprises SET
          name = ?,
          name2 = ?,
          bot_token = ?,
          chat_id = ?,
          ip = ?,
          secret = ?,
          host = ?
        WHERE number = ?
        """,
        (name, name2, bot_token, chat_id, ip, secret, host, number)
    )
    conn.commit()
    conn.close()

def delete_enterprise(number: str):
    conn = get_db_connection()
    conn.execute("DELETE FROM enterprises WHERE number = ?", (number,))
    conn.commit()
    conn.close()
