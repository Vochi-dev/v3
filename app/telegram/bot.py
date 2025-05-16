# app/telegram/bot.py
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.services.database import get_enterprises_with_tokens
from app.telegram.handlers import onboarding

# ───────── настройка логирования ─────────
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logger.addHandler(console_handler)

file_handler = logging.FileHandler("client_bots.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(file_handler)

async def run_bot(token: str):
    bot = Bot(token=token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(onboarding.router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info(f"Webhook removed, switched to long-polling")
        logger.info(f"Bot started for enterprise unknown")
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception(f"Ошибка запуска бота с токеном {token}: {e}")

async def main():
    enterprises = await get_enterprises_with_tokens()
    tasks = [run_bot(e["bot_token"]) for e in enterprises if e["bot_token"]]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
