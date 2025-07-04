from app.services.postgres import get_pool


def init_database_tables():
    """
    Функция больше не нужна - используем существующие PostgreSQL таблицы
    """
    pass


async def get_connection():
    """
    Функция для получения асинхронного подключения к базе данных PostgreSQL.
    """
    pool = await get_pool()
    if pool:
        return await pool.acquire()
    return None


async def get_enterprise_number_by_bot_token(bot_token: str) -> str:
    """
    Получение номера предприятия по bot_token.
    """
    pool = await get_pool()
    if not pool:
        return None
        
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT number FROM enterprises WHERE bot_token = $1", bot_token
        )
    return row["number"] if row else None


async def get_enterprise_name_by_number(enterprise_number: str) -> str | None:
    """
    Получение названия предприятия по номеру.
    """
    pool = await get_pool()
    if not pool:
        return None
        
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name FROM enterprises WHERE number = $1", enterprise_number
        )
    return row["name"] if row else None

async def get_all_bot_tokens() -> dict:
    """
    Возвращает словарь {enterprise_number: bot_token} для всех предприятий с непустыми токенами.
    """
    result = {}
    pool = await get_pool()
    if not pool:
        return result
        
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT number, bot_token FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != ''"
        )
        for row in rows:
            result[row["number"]] = row["bot_token"]
    return result
