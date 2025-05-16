import aiosqlite

DB_PATH = "/root/asterisk-webhook/asterisk_events.db"

async def get_enterprises_with_tokens():
    enterprises = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT number, bot_token, chat_id, active FROM enterprises") as cursor:
            async for row in cursor:
                enterprises.append({
                    "number": row["number"],
                    "bot_token": row["bot_token"],
                    "chat_id": row["chat_id"],
                    "active": row["active"]
                })
    return enterprises
