# app/services/db.py
import aiosqlite
from typing import Optional

from app.config import DB_PATH        # убедитесь, что в config.py есть переменная DB_PATH


async def get_enterprise_by_bot_token(bot_token: str) -> Optional[aiosqlite.Row]:
    """
    Верни строку из таблицы enterprises по bot_token.
    Если ничего не нашли — верни None.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name FROM enterprises WHERE bot_token = ?",
            (bot_token,),
        ) as cur:
            return await cur.fetchone()
