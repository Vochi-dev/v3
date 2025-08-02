import asyncio
import logging
import sys
import argparse

from aiogram import Bot, Dispatcher, types
from aiogram.utils.exceptions import TelegramAPIError

from app.services.db import get_enterprise_name_by_number, get_all_bot_tokens

logger = logging.getLogger(__name__)

# 🔴 Удалено: def load_bot_token(...)

async def run_bot(enterprise_number: str):
    # Получаем все токены и находим нужный
    all_tokens = await get_all_bot_tokens()
    BOT_TOKEN = all_tokens.get(enterprise_number)
    
    if not BOT_TOKEN:
        logger.error(f"No bot token found for enterprise {enterprise_number}")
        sys.exit(1)

    bot = Bot(token=BOT_TOKEN)
    
    # ✅ СОЗДАЕМ DISPATCHER С AUTH SUPPORT (aiogram 2.x)
    from aiogram import Dispatcher
    from aiogram.contrib.fsm_storage.memory import MemoryStorage
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from telegram_auth_handler_v2 import register_auth_handlers
    
    dp = Dispatcher(bot, storage=MemoryStorage())
    
    # Сохраняем enterprise_number в dispatcher для доступа из handlers
    dp['enterprise_number'] = enterprise_number
    
    register_auth_handlers(dp, enterprise_number)

    try:
        logger.info(f"Bot for enterprise {enterprise_number} started with AUTH support.")
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
