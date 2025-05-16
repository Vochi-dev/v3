import aiosqlite
import logging

logger = logging.getLogger(__name__)
DB_PATH = "/root/asterisk-webhook/asterisk_events.db"

async def get_enterprises_with_tokens():
    query = """
        SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2
        FROM enterprises
        WHERE bot_token IS NOT NULL AND bot_token != ''
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                enterprises = [dict(row) for row in rows]
                logger.debug(f"🔄 Загружено {len(enterprises)} предприятий с токенами.")
                return enterprises
    except Exception as e:
        logger.exception(f"❌ Ошибка при получении предприятий с токенами: {e}")
        return []
