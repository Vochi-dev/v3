import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.dispatcher.filters import Command

from app.services.database import get_enterprises_with_tokens

logger = logging.getLogger(__name__)

async def start_handler(message: types.Message):
    await message.answer("Привет! Я ваш бот.")

async def start_bot(enterprise_number: str, token: str):
    bot = Bot(token=token, parse_mode=types.ParseMode.HTML)
    dp = Dispatcher(bot)

    # Регистрируем хендлеры (aiogram 2.x)
    dp.register_message_handler(start_handler, commands=["start"])

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
        # Запуск polling (в синхронном виде через executor)
        # Чтобы запустить несколько ботов параллельно, надо использовать create_task, 
        # но в aiogram 2.x лучше делать это по-другому — для простоты оставим так:
        await dp.start_polling()
    except Exception as e:
        logger.exception(f"❌ Ошибка во время polling для бота {enterprise_number}: {e}")

async def start_enterprise_bots():
    enterprises = await get_enterprises_with_tokens()
    tasks = []
    for ent in enterprises:
        number = ent["number"]
        token = ent["bot_token"]
        # Запускаем боты параллельно
        tasks.append(asyncio.create_task(start_bot(number, token)))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_enterprise_bots())
