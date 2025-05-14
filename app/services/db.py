# app/services/db.py
# -*- coding: utf-8 -*-
"""
Обёртки вокруг SQLite — под фактическую схему (enterprises.number) и утилиты для роутеров.
"""

import aiosqlite
from typing import Optional

from app.config import DB_PATH


async def get_enterprise_number_by_bot_token(bot_token: str) -> Optional[str]:
    """
    Возвращает номер (enterprises.number) для данного bot_token
    или None, если не нашли.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT number FROM enterprises WHERE bot_token = ?",
            (bot_token,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_connection() -> aiosqlite.Connection:
    """
    Возвращает асинхронное соединение к базе данных.
    Используется в роутерах для общих операций.
    """
    conn = await aiosqlite.connect(DB_PATH)
    # чтобы получать строки как dict-подобные объекты
    conn.row_factory = aiosqlite.Row
    return conn
