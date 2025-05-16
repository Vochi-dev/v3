# app/telegram/bot.py
import sys
import logging
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.config import TELEGRAM_BOT_TOKEN
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

file_handler = logging.FileHandler("telegram_bot.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(file_handler)

# ───────── запуск бота ─────────
async def main():
    logger.info("Initializing bot...")
    bot = Bot(token=TELEGRAM_BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(onboarding.router)

    logger.info(f"Bot started for enterprise unknown")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
