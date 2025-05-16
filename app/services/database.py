import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from app.services.database import get_enterprises_with_tokens
from app.telegram.onboarding import router as onboarding_router

# Базовая конфигурация логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("asterisk_events.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def start_bot(enterprise_number: str, token: str):
    bot = Bot(token=token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()

    dp.include_router(onboarding_router)

    # Подробное логирование для конкретного бота
    if enterprise_number == "0201":
        logger.setLevel(logging.DEBUG)
        logging.getLogger("aiogram").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logger.debug("🔍 Подробное логирование включено для бота 0201")
        logger.debug(f"Токен: {token}")

    try:
        me = await bot.get_me()
        logger.info(f"✅ Бот {enterprise_number} запущен: @{me.username}")
    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации бота {enterprise_number}: {e}")
        return

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception(f"❌ Ошибка во время polling для бота {enterprise_number}: {e}")

async def main():
    enterprises = await get_enterprises_with_tokens()
    tasks = []
    for enterprise in enterprises:
        number = enterprise["number"]
        token = enterprise["bot_token"]
        if number == "0201":  # Только бот 0201
            tasks.append(start_bot(number, token))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
