import asyncio
from aiogram import Bot

async def main():
    bot = Bot('8133181812:AAH_Ty_ndTeO8Y_NlTEFkbBsgGIrGUlH5I0')
    me = await bot.get_me()
    print(f'✅ Бот 0201 активен (@{me.username})')
    await bot.session.close()

asyncio.run(main())
