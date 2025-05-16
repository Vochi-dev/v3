# app/telegram/bot.py
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from app.config import DB_PATH
from app.telegram.handlers.onboarding import router as onboarding_router
from app.services.database import get_enterprises_with_tokens

# ───────── настройка логирования ─────────
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logger.addHandler(console_handler)

# База и файл логов для INFO
file_handler = logging.FileHandler("telegram_bots.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(file_handler)

logging.getLogger("aiogram").setLevel(logging.DEBUG)


async def start_enterprise_bots():
    enterprises = await get_enterprises_with_tokens()
    for ent in enterprises:
        try:
            token = ent["bot_token"]
            bot = Bot(token=token)
            dp = Dispatcher()
            dp.include_router(onboarding_router)
            asyncio.create_task(dp.start_polling(bot))
            logger.info(f"✅ Бот {token[:8]}... запущен")
        except Exception as e:
            logger.exception(f"❌ Ошибка запуска бота {ent['number']}: {e}")
