import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import ParseMode
from aiogram.dispatcher.filters import Command
from aiogram.types import Message
from aiogram.dispatcher.router import Router

from app.services.database import get_enterprises_with_tokens

logger = logging.getLogger(__name__)

# Создаем роутер один раз
onboarding_router = Router()

@onboarding_router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("Привет! Я ваш бот.")

async def start_bot(enterprise_number: str, token: str):
    bot = Bot(token=token, default=ParseMode.HTML)
    dp = Dispatcher(bot)  # Обязательно привязываем к боту!

    # Подключаем роутер к диспетчеру (делаем это единожды)
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
        await dp.start_polling()
    except Exception as e:
        logger.exception(f"❌ Ошибка во время polling для бота {enterprise_number}: {e}")

async def start_enterprise_bots():
    enterprises = await get_enterprises_with_tokens()
    tasks = []
    for ent in enterprises:
        number = ent["number"]
        token = ent["bot_token"]
        tasks.append(start_bot(number, token))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_enterprise_bots())
