# app/services/enterprise.py
# -*- coding: utf-8 -*-

import sqlite3
from datetime import datetime
import asyncio
from telegram import Bot
from telegram.error import TelegramError

from app.config import DB_PATH

def list_enterprises():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM enterprises ORDER BY number")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_enterprise(number: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM enterprises WHERE number = ?", (number,))
    row = cur.fetchone()
    conn.close()
    return row

def create_enterprise(number: str, name: str, name2: str, bot_token: str,
                      chat_id: str, ip: str, secret: str, host: str):
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM enterprises WHERE number = ?", (number,))
    conn.commit()
    conn.close()

async def send_message_to_bot(bot_token: str, chat_id: str, message: str):
    """
    Асинхронно отправляет сообщение в Telegram-бота по bot_token и chat_id.
    Возвращает (True, None) при успехе, (False, error_message) при ошибке.
    """
    bot = Bot(token=bot_token)
    try:
        await bot.send_message(chat_id=int(chat_id), text=message)
        return True, None
    except TelegramError as e:
        # Возвращаем описание ошибки
        return False, str(e)
