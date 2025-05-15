# app/telegram/bot.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotCommands
from aiogram.exceptions import TelegramNotFound
from app.telegram.handlers.onboarding import router as onboarding_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def setup_bot():
    # Создаём Bot по токену из окружения
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Попытка удалить webhook, если он есть
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook removed, switched to long-polling")
    except TelegramNotFound:
        logger.info("No webhook to delete, continuing with long-polling")
    except Exception as e:
        logger.warning("Error deleting webhook: %s", e)

    # Регистрируем команды по умолчанию (опционально)
    await bot.set_my_commands(DefaultBotCommands(descriptors=[
        ("start", "Начать регистрацию"),
    ]))

    # Подключаем маршруты
    dp.include_router(onboarding_router)

    # Сохраняем номер предприятия в контексте (если нужно)
    enterprise_number = await get_enterprise_number_by_bot_token(bot.token)
    logger.info("Bot started for enterprise %s", enterprise_number)

    return bot, dp


async def main():
    bot, dp = await setup_bot()
    # Стартуем polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
