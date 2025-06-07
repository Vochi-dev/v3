import asyncpg
import logging
from app.config import (
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_HOST,
    POSTGRES_PORT
)

logger = logging.getLogger(__name__)

pool = None

async def init_pool():
    global pool
    if pool is None:
        try:
            pool = await asyncpg.create_pool(
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                database=POSTGRES_DB,
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                min_size=1,
                max_size=10
            )
            logger.info("Connection pool to PostgreSQL created successfully.")
        except Exception as e:
            logger.exception(f"Failed to create PostgreSQL connection pool: {e}")
            raise

async def close_pool():
    global pool
    if pool:
        await pool.close()
        logger.info("Connection pool to PostgreSQL closed.")
        pool = None

async def get_all_enterprises_postgresql():
    if pool is None:
        await init_pool()
    async with pool.acquire() as connection:
        query = """
            SELECT
                number,
                name,
                is_enabled AS active,
                created_at,
                updated_at,
                last_activity,
                name2,
                secret,
                bot_token,
                chat_id,
                ip,
                host
            FROM enterprises
            ORDER BY CAST(number AS INTEGER) ASC;
        """
        rows = await connection.fetch(query)
        return [dict(row) for row in rows] 