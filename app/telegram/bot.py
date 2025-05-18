import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.utils.markdown import hbold

from app.services.database import get_enterprises_with_tokens

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def start_handler(message: Message, enterprise_number: str):
    text = f"Привет! Я ваш бот.\nВаш номер предприятия: {hbold(enterprise_number)}"
    await message.answer(text)


async def create_bot_instance(bot_token: str, enterprise_number: str):
    bot = Bot(token=bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message()
    async def handle_all_messages(message: Message):
        if message.text == "/start":
            await start_handler(message, enterprise_number)
        else:
            await message.answer("Команда не распознана.")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info(f"✅ Бот {enterprise_number} запущен: @{me.username}")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации бота {enterprise_number}: {e}")


async def start_enterprise_bots():
    enterprises = await get_enterprises_with_tokens()
    if not enterprises:
        logger.warning("Нет ни одного активного предприятия с токеном для запуска бота.")
        return

    tasks = []
    for ent in enterprises:
        # Преобразуем Row в dict для корректной работы с .get()
        ent = dict(ent)
        bot_token = ent.get("bot_token", "").strip()
        number = ent.get("number", "").strip()
        if bot_token and number:
            logger.debug(f"Запускаем бота для предприятия #{number} с токеном: {bot_token[:5]}...")
            tasks.append(create_bot_instance(bot_token, number))
        else:
            logger.warning(f"Пропуск предприятия {ent} — отсутствует bot_token или number")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(start_enterprise_bots())
