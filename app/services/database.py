import aiosqlite
from app.config import DB_PATH

async def execute(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()

async def fetchall(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

async def fetchone(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

async def get_enterprises_with_tokens():
    sql = """
        SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2
        FROM enterprises
        WHERE bot_token IS NOT NULL AND bot_token != ''
        ORDER BY CAST(number AS INTEGER) ASC
    """
    return await fetchall(sql)
