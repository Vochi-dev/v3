# app/telegram/bot.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.exceptions import TelegramNotFound

from app.config import TELEGRAM_BOT_TOKEN
from app.services.db import get_enterprise_number_by_bot_token
from app.telegram.handlers.onboarding import router as onboarding_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def setup_bot():
    # Инициируем Bot и Dispatcher
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Попытка удалить старый webhook
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook removed, switched to long-polling")
    except TelegramNotFound:
        logger.info("No webhook to delete, continuing with long-polling")
    except Exception as e:
        logger.warning("Error deleting webhook: %s", e)

    # Регистрируем команду /start
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать регистрацию"),
    ])

    # Подключаем хэндлеры
    dp.include_router(onboarding_router)

    # Логируем enterprise_number для этого бота
    enterprise_number = await get_enterprise_number_by_bot_token(TELEGRAM_BOT_TOKEN)
    logger.info("Bot started for enterprise %s", enterprise_number or "unknown")

    return bot, dp


async def main():
    bot, dp = await setup_bot()
    # Старт long-polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
