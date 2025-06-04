import asyncio
import logging
from app.services.postgres import get_pool

logger = logging.getLogger(__name__)

async def init_database():
    """Инициализирует базу данных PostgreSQL"""
    logger.info("Начало инициализации базы данных PostgreSQL")
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        # Создаем таблицу предприятий
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS enterprises (
                number TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                bot_token TEXT,
                chat_id TEXT,
                ip TEXT,
                secret TEXT,
                host TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                name2 TEXT,
                active INTEGER DEFAULT 1
            )
        """)
        
        # Создаем таблицу для telegram пользователей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_users (
                id SERIAL PRIMARY KEY,
                tg_id TEXT NOT NULL,
                bot_token TEXT NOT NULL,
                email TEXT,
                verified INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем таблицу для email пользователей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_users (
                id SERIAL PRIMARY KEY,
                number TEXT,
                email TEXT NOT NULL UNIQUE,
                name TEXT,
                right_all INTEGER DEFAULT 0,
                right_1 INTEGER DEFAULT 0,
                right_2 INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем таблицу для связи предприятий и пользователей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS enterprise_users (
                id SERIAL PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                telegram_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (enterprise_id) REFERENCES enterprises(number)
            )
        """)
        
    logger.info("База данных PostgreSQL успешно инициализирована")

if __name__ == "__main__":
    asyncio.run(init_database()) 