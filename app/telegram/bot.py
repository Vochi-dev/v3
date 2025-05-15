# app/telegram/bot.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNotFound

from app.config import TELEGRAM_BOT_TOKEN
from app.services.db import get_enterprise_number_by_bot_token
from app.telegram.handlers.onboarding import router as onboarding_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def setup_bot():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Удаляем webhook, если он есть
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook removed, switched to long-polling")
    except TelegramNotFound:
        logger.info("No webhook to delete, continuing with long-polling")
    except Exception as e:
        logger.warning("Error deleting webhook: %s", e)

    # Регистрируем хэндлеры
    dp.include_router(onboarding_router)

    # Логируем enterprise_number
    try:
        ent_num = await get_enterprise_number_by_bot_token(TELEGRAM_BOT_TOKEN)
        logger.info("Bot started for enterprise %s", ent_num or "unknown")
    except Exception as e:
        logger.warning("Error fetching enterprise_number: %s", e)

    return bot, dp


async def main():
    bot, dp = await setup_bot()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
