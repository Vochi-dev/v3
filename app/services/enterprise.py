# -*- coding: utf-8 -*-
import aiosqlite
import logging
from typing import Optional, List, Dict, Any

from app.config import DB_PATH

logger = logging.getLogger("enterprise")


async def get_enterprise_by_number(number: str) -> Optional[Dict[str, Any]]:
    """
    Получить предприятие по номеру.
    Возвращает словарь с данными предприятия или None, если не найдено.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT number, name, bot_token, active, chat_id, ip, secret, host, created_at, name2 "
            "FROM enterprises WHERE number = ?", (number,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row:
            return dict(row)
        return None


async def list_enterprises() -> List[Dict[str, Any]]:
    """
    Получить список всех предприятий отсортированных по числовому номеру.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT number, name, bot_token, active, chat_id, ip, secret, host, created_at, name2 "
            "FROM enterprises ORDER BY CAST(number AS INTEGER) ASC"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]


async def add_enterprise(
    number: str,
    name: str,
    bot_token: str,
    chat_id: str,
    ip: str,
    secret: str,
    host: str,
    name2: Optional[str] = "",
):
    """
    Добавить новое предприятие в базу.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO enterprises "
                "(number, name, bot_token, chat_id, ip, secret, host, created_at, name2) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (number, name, bot_token, chat_id, ip, secret, host, _current_time_str(), name2),
            )
            await db.commit()
            logger.info(f"Добавлено новое предприятие: {number} - {name}")
        except Exception as e:
            logger.error(f"Ошибка при добавлении предприятия {number}: {e}")
            raise


async def update_enterprise(
    number: str,
    name: str,
    bot_token: str,
    chat_id: str,
    ip: str,
    secret: str,
    host: str,
    name2: Optional[str] = "",
):
    """
    Обновить данные предприятия по номеру.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "UPDATE enterprises SET "
                "name = ?, bot_token = ?, chat_id = ?, ip = ?, secret = ?, host = ?, name2 = ? "
                "WHERE number = ?",
                (name, bot_token, chat_id, ip, secret, host, name2, number),
            )
            await db.commit()
            logger.info(f"Обновлено предприятие: {number} - {name}")
        except Exception as e:
            logger.error(f"Ошибка при обновлении предприятия {number}: {e}")
            raise


async def delete_enterprise(number: str):
    """
    Удалить предприятие по номеру из базы.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("DELETE FROM enterprises WHERE number = ?", (number,))
            await db.commit()
            logger.info(f"Удалено предприятие: {number}")
        except Exception as e:
            logger.error(f"Ошибка при удалении предприятия {number}: {e}")
            raise


async def exists_enterprise(number: str) -> bool:
    """
    Проверить, существует ли предприятие с данным номером.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM enterprises WHERE number = ?", (number,))
        result = await cursor.fetchone()
        await cursor.close()
        return result is not None


def _current_time_str() -> str:
    """
    Возвращает текущее время в формате ISO для хранения в базе.
    """
    from datetime import datetime
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


# Дополнительные утилиты, если необходимы:

async def count_enterprises() -> int:
    """
    Получить количество предприятий в базе.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM enterprises")
        (count,) = await cursor.fetchone()
        await cursor.close()
        return count


# Пример функции для поиска по части имени (при необходимости)

async def search_enterprises_by_name(query: str) -> List[Dict[str, Any]]:
    """
    Поиск предприятий по части имени (LIKE %query%)
    """
    like_pattern = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT number,# app/services/enterprise.py
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
 name, bot_token, active, chat_id, ip, secret, host, created_at, name2 "
            "FROM enterprises WHERE name LIKE ? ORDER BY CAST(number AS INTEGER) ASC",
            (like_pattern,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

