# app/services/enterprise.py
# -*- coding: utf-8 -*-

import sqlite3
from datetime import datetime

from app.config import DB_PATH

def list_enterprises():
    """
    Возвращает все предприятия, отсортированные по номеру.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM enterprises ORDER BY number"
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def get_enterprise(number: str):
    """
    Возвращает одно предприятие по его number, или None.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM enterprises WHERE number = ?",
        (number,)
    )
    row = cur.fetchone()
    conn.close()
    return row

def create_enterprise(number: str, name: str, name2: str, bot_token: str,
                      chat_id: str, ip: str, secret: str, host: str):
    """
    Добавляет новое предприятие.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO enterprises
          (number, name, name2, bot_token, chat_id, ip, secret, host, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (number, name, name2, bot_token, chat_id, ip, secret, host, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def update_enterprise(number: str, name: str, name2: str, bot_token: str,
                      chat_id: str, ip: str, secret: str, host: str):
    """
    Обновляет поля существующего предприятия.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        UPDATE enterprises SET
          name     = ?,
          name2    = ?,
          bot_token= ?,
          chat_id  = ?,
          ip       = ?,
          secret   = ?,
          host     = ?
        WHERE number = ?
        """,
        (name, name2, bot_token, chat_id, ip, secret, host, number)
    )
    conn.commit()
    conn.close()

def delete_enterprise(number: str):
    """
    Удаляет предприятие по его number.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "DELETE FROM enterprises WHERE number = ?",
        (number,)
    )
    conn.commit()
    conn.close()
