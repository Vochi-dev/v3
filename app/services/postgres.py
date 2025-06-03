import asyncpg
from typing import List, Dict, Optional
from datetime import datetime

# Конфигурация подключения
POSTGRES_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

# Глобальный пул подключений
_pool = None

async def init_pool():
    """Инициализирует глобальный пул подключений"""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            min_size=2,      # минимальное количество подключений
            max_size=10,     # максимальное количество подключений
            **POSTGRES_CONFIG
        )

async def get_pool():
    """Возвращает существующий пул подключений или создает новый"""
    if _pool is None:
        await init_pool()
    return _pool

async def close_pool():
    """Закрывает пул подключений"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

# Функции для работы с предприятиями
async def get_all_enterprises():
    """Получает список всех предприятий"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT number, name, bot_token, chat_id, ip, secret, host, 
                   created_at, name2, active
            FROM enterprises
            ORDER BY CAST(number AS INTEGER) ASC
        """)
        return [dict(row) for row in rows]

async def get_enterprise_by_number(number: str):
    """Получает предприятие по номеру"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT number, name, bot_token, chat_id, ip, secret, host,
                   created_at, name2, active
            FROM enterprises
            WHERE number = $1
            LIMIT 1
        """, number)
        return dict(row) if row else None

async def add_enterprise(number: str, name: str, bot_token: str, chat_id: str,
                        ip: str, secret: str, host: str, name2: str = ''):
    """Добавляет новое предприятие"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO enterprises (
                number, name, bot_token, chat_id, ip, secret, host, 
                created_at, name2
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, number, name, bot_token, chat_id, ip, secret, host,
        datetime.utcnow(), name2)

async def update_enterprise(number: str, name: str, bot_token: str, chat_id: str,
                          ip: str, secret: str, host: str, name2: str = '',
                          active: Optional[int] = None):
    """Обновляет информацию о предприятии"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if active is None:
            await conn.execute("""
                UPDATE enterprises
                SET name = $1, bot_token = $2, chat_id = $3,
                    ip = $4, secret = $5, host = $6, name2 = $7
                WHERE number = $8
            """, name, bot_token, chat_id, ip, secret, host, name2, number)
        else:
            await conn.execute("""
                UPDATE enterprises
                SET name = $1, bot_token = $2, chat_id = $3,
                    ip = $4, secret = $5, host = $6, name2 = $7,
                    active = $8
                WHERE number = $9
            """, name, bot_token, chat_id, ip, secret, host, name2, active, number)

async def delete_enterprise(number: str):
    """Удаляет предприятие"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM enterprises WHERE number = $1", number)

async def get_enterprises_with_tokens():
    """Получает список предприятий с активными токенами"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT number, name, bot_token, chat_id, ip, secret, host,
                   created_at, name2, active
            FROM enterprises
            WHERE bot_token IS NOT NULL 
              AND chat_id IS NOT NULL 
              AND TRIM(bot_token) != '' 
              AND TRIM(chat_id) != ''
              AND active = 1
            ORDER BY CAST(number AS INTEGER) ASC
        """)
        return [dict(row) for row in rows]

async def get_enterprise_number_by_bot_token(bot_token: str) -> str:
    """Получает номер предприятия по токену бота"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT number FROM enterprises WHERE bot_token = $1",
            bot_token
        )
        return row['number'] if row else None 