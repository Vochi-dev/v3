import asyncio
import logging
import sys
import argparse

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

def load_bot_token(enterprise_number):
    tokens = {
        "0100": "7765204924:AAEFCyUsxGhTWsuIENX47iqpD3s8L60kwmc",
        "0201": "8133181812:AAH_Ty_ndTeO8Y_NlTEFkbBsgGIrGUlH5I0",
        "0262": "8040392268:AAG_YuBqy7n1_nX6Cvte70--draQ21S2Cvs",
    }
    return tokens.get(enterprise_number)

async def run_bot(enterprise_number: str):
    BOT_TOKEN = load_bot_token(enterprise_number)
    if not BOT_TOKEN:
        logger.error(f"No bot token found for enterprise {enterprise_number}")
        sys.exit(1)

    from aiogram.client.session import DefaultBotSession
    from aiogram.client.bot import DefaultBotProperties

    bot = Bot(
        token=BOT_TOKEN,
        session=DefaultBotSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    @dp.message(Command(commands=["start"]))
    async def cmd_start(message: types.Message):
        await message.answer(f"Привет! Бот предприятия {enterprise_number} запущен и готов к работе.")

    try:
        logger.info(f"Bot for enterprise {enterprise_number} started.")
        await dp.start_polling(bot)
    except TelegramAPIError as e:
        logger.error(f"Telegram API error: {e}")
    finally:
        await bot.session.close()

def main():
    parser = argparse.ArgumentParser(description="Telegram Bot for Enterprise")
    parser.add_argument('--enterprise', type=str, required=True, help='Enterprise number')
    args = parser.parse_args()

    enterprise_number = args.enterprise
    asyncio.run(run_bot(enterprise_number))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
