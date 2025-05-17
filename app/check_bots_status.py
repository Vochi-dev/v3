import asyncio
from telegram import Bot
from app.services.db import get_connection

async def check_status():
    db = await get_connection()
    db.row_factory = None
    cur = await db.execute("SELECT number, bot_token FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != ''")
    rows = await cur.fetchall()
    await db.close()

    for number, token in rows:
        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            print(f"✅ Бот #{number} активен (@{me.username})")
        except Exception:
            print(f"❌ Бот #{number} неактивен")

asyncio.run(check_status())
