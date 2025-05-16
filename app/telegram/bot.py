import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.services.database import get_enterprises_with_tokens
from app.telegram.onboarding import router as onboarding_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bots_tasks = {}

async def start_bot(enterprise_number: str, token: str):
    bot = Bot(token=token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(onboarding_router)

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

async def start_enterprise_bots():
    enterprises = await get_enterprises_with_tokens()  # <-- исправлено
    tasks = []
    for enterprise in enterprises:
        number = enterprise["number"]
        token = enterprise["bot_token"]
        task = asyncio.create_task(start_bot(number, token))
        bots_tasks[number] = task
        tasks.append(task)
    await asyncio.gather(*tasks)

# для ручного запуска
if __name__ == "__main__":
    asyncio.run(start_enterprise_bots())
