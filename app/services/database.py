import aiosqlite
from app.config import DB_PATH

async def execute(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()

async def fetchall(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # Чтобы получить dict-подобные строки
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

async def fetchone(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # Чтобы получить dict-подобный результат
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

async def get_enterprise_by_number(number: str):
    sql = """
        SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2
        FROM enterprises
        WHERE number = ?
        LIMIT 1
    """
    return await fetchone(sql, (number,))

async def update_enterprise(number: str, name: str, bot_token: str, chat_id: str, ip: str, secret: str, host: str, name2: str = ''):
    sql = """
        UPDATE enterprises
        SET name = ?, bot_token = ?, chat_id = ?, ip = ?, secret = ?, host = ?, name2 = ?
        WHERE number = ?
    """
    await execute(sql, (name, bot_token, chat_id, ip, secret, host, name2, number))

async def add_enterprise(number: str, name: str, bot_token: str, chat_id: str, ip: str, secret: str, host: str, name2: str = ''):
    sql = """
        INSERT INTO enterprises (number, name, bot_token, chat_id, ip, secret, host, created_at, name2)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
    """
    await execute(sql, (number, name, bot_token, chat_id, ip, secret, host, name2))

async def delete_enterprise(number: str):
    sql = """
        DELETE FROM enterprises
        WHERE number = ?
    """
    await execute(sql, (number,))
