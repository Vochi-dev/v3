import asyncio
import logging
import sys
import argparse

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError

from app.services.db import get_bot_token_by_number, get_enterprise_name_by_number  # 👈 добавлено

logger = logging.getLogger(__name__)

# 🔴 Удалено: def load_bot_token(...)

async def run_bot(enterprise_number: str):
    BOT_TOKEN = await get_bot_token_by_number(enterprise_number)  # 👈 получаем токен из базы
    if not BOT_TOKEN:
        logger.error(f"No bot token found for enterprise {enterprise_number}")
        sys.exit(1)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    @dp.message(Command(commands=["start"]))
    async def cmd_start(message: types.Message):
        name = await get_enterprise_name_by_number(enterprise_number)  # 👈 получаем имя компании
        if not name:
            name = enterprise_number
        await message.answer(f"Бот компании {name} запущен. Техподдержка @VochiSupport")

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
