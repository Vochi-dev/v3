# app/services/database.py

import aiosqlite
from app.config import DB_PATH


async def get_enterprises_with_tokens():
    query = """
        SELECT number, name, bot_token, chat_id, ip, secret, host, name2
        FROM enterprises
        WHERE bot_token IS NOT NULL AND bot_token != ''
    """

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
