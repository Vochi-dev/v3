import aiosqlite
from app.config import DATABASE_PATH

async def execute(sql, params=()):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(sql, params)
        await db.commit()

async def fetchall(sql, params=()):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

async def fetchone(sql, params=()):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row
