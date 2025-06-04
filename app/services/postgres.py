import asyncpg
from typing import List, Dict, Optional
from datetime import datetime
import logging
import sys
import os

# Конфигурация логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(console_handler)

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

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'postgres.log')

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
    print(f"POSTGRES_GET_BY_NUMBER: Вызвана для номера: '{number}' (тип: {type(number)})", file=sys.stderr, flush=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql_query = """
            SELECT number, name, bot_token, chat_id, ip, secret, host,
                   created_at, name2, active
            FROM enterprises
            WHERE number = $1
            LIMIT 1
        """
        print(f"POSTGRES_GET_BY_NUMBER: Выполняется SQL: {sql_query} с параметром: '{number}'", file=sys.stderr, flush=True)
        row = await conn.fetchrow(sql_query, number)
        print(f"POSTGRES_GET_BY_NUMBER: Результат fetchrow: {row} (тип: {type(row)})", file=sys.stderr, flush=True)
        if row:
            print(f"POSTGRES_GET_BY_NUMBER: Предприятие найдено, возвращаем dict(row)", file=sys.stderr, flush=True)
            return dict(row)
        else:
            print(f"POSTGRES_GET_BY_NUMBER: Предприятие НЕ найдено, возвращаем None", file=sys.stderr, flush=True)
            return None

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

def debug_log(message):
    os.system(f'echo "{message}" >> /root/asterisk-webhook/debug.txt')

async def update_enterprise(number: str, name: str, bot_token: str, chat_id: str,
                          ip: str, secret: str, host: str, name2: str = '',
                          active: Optional[int] = None):
    """Обновляет информацию о предприятии"""
    print(f"POSTGRES: Начало обновления предприятия {number}")
    print(f"POSTGRES: Параметры: name={name}, ip={ip}, host={host}, name2={name2}")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if active is None:
                print(f"POSTGRES: Выполняем UPDATE без active для предприятия {number}")
                result = await conn.execute("""
                    UPDATE enterprises
                    SET name = $1, bot_token = $2, chat_id = $3,
                        ip = $4, secret = $5, host = $6, name2 = $7
                    WHERE number = $8
                """, name, bot_token, chat_id, ip, secret, host, name2, number)
                print(f"POSTGRES: Результат UPDATE: {result}")
            else:
                print(f"POSTGRES: Выполняем UPDATE с active={active} для предприятия {number}")
                result = await conn.execute("""
                    UPDATE enterprises
                    SET name = $1, bot_token = $2, chat_id = $3,
                        ip = $4, secret = $5, host = $6, name2 = $7,
                        active = $8
                    WHERE number = $9
                """, name, bot_token, chat_id, ip, secret, host, name2, active, number)
                print(f"POSTGRES: Результат UPDATE: {result}")
    except Exception as e:
        print(f"POSTGRES ERROR: {str(e)}")
        raise

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

async def get_enterprise_by_name2_suffix(name2_suffix: str) -> Optional[Dict]:
    """
    Получает предприятие, у которого поле name2 заканчивается на указанный суффикс.
    Возвращает первую найденную запись или None.
    """
    print(f"POSTGRES_GET_BY_NAME2_SUFFIX: Вызвана для суффикса: '{name2_suffix}'", file=sys.stderr, flush=True)
    pool = await get_pool()
    if not pool:
        print("POSTGRES_GET_BY_NAME2_SUFFIX ERROR: Пул не инициализирован", file=sys.stderr, flush=True)
        return None
        
    async with pool.acquire() as conn:
        sql_query = """
            SELECT number, name, bot_token, chat_id, name2, active
            FROM enterprises
            WHERE name2 LIKE $1
            ORDER BY id ASC  -- Или другая логика сортировки, если нужно выбрать конкретное из нескольких
            LIMIT 1
        """
        # Для LIKE '%suffix' параметр должен быть '%suffix'
        param_suffix = '%' + name2_suffix
        print(f"POSTGRES_GET_BY_NAME2_SUFFIX: Выполняется SQL: {sql_query.strip()} с параметром: '{param_suffix}'", file=sys.stderr, flush=True)
        row = await conn.fetchrow(sql_query, param_suffix)
        print(f"POSTGRES_GET_BY_NAME2_SUFFIX: Результат fetchrow: {row}", file=sys.stderr, flush=True)
        if row:
            return dict(row)
        else:
            return None 