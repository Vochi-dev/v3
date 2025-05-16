import aiosqlite
from typing import List, Dict

DB_PATH = "/root/asterisk-webhook/asterisk_events.db"

async def get_enterprises_with_tokens() -> List[Dict[str, str]]:
    enterprises = []
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT number, bot_token FROM enterprises WHERE active = 1"
        )
        rows = await cursor.fetchall()
        await cursor.close()

        for row in rows:
            enterprises.append({
                "number": row[0],
                "bot_token": row[1]
            })

    return enterprises
