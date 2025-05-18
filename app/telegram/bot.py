import asyncio
import logging
import sys
import argparse

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ParseMode
from aiogram.utils.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

# Парсим аргументы для запуска бота конкретного предприятия
parser = argparse.ArgumentParser(description="Telegram Bot for Enterprise")
parser.add_argument('--enterprise', type=str, required=True, help='Enterprise number')
args = parser.parse_args()

ENTERPRISE_NUMBER = args.enterprise

# Здесь загрузка токена для конкретного предприятия из БД или конфига
# Для примера загружаем из файла, лучше — из базы данных
def load_bot_token(enterprise_number):
    # TODO: заменить на загрузку из БД
    # Пример жестко захардкожен, замени на логику из БД
    tokens = {
        "0100": "7765204924:AAEFCyUsxGhTWsuIENX47iqpD3s8L60kwmc",
        "0201": "8133181812:AAH_Ty_ndTeO8Y_NlTEFkbBsgGIrGUlH5I0",
        "0262": "8040392268:AAG_YuBqy7n1_nX6Cvte70--draQ21S2Cvs",
    }
    return tokens.get(enterprise_number)

BOT_TOKEN = load_bot_token(ENTERPRISE_NUMBER)
if not BOT_TOKEN:
    logger.error(f"No bot token found for enterprise {ENTERPRISE_NUMBER}")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    await message.answer(f"Привет! Бот предприятия {ENTERPRISE_NUMBER} запущен и готов к работе.")

async def main():
    try:
        logger.info(f"Bot for enterprise {ENTERPRISE_NUMBER} started.")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
