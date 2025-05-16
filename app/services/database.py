# app/telegram/bot.py
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from app.telegram.onboarding import router as onboarding_router
from app.services.database import get_enterprises_with_tokens

# ────────── Логирование ──────────
logger = logging.getLogger("client-bots")
logger.setLevel(logging.DEBUG)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
)

file_handler = logging.FileHandler("client_bots.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
)

logger.addHandler(stream_handler)
logger.addHandler(file_handler)


async def run_bot(token: str, number: str):
    try:
        bot = Bot(token=token)
        dp = Dispatcher()
        dp.include_router(onboarding_router)

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info(f"Webhook удалён, бот {number} переключён на polling")

        logger.info(f"Старт бота {number}")
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception(f"Ошибка при запуске бота {number}: {e}")


async def main():
    enterprises = await get_enterprises_with_tokens()
    tasks = []

    if not enterprises:
        logger.warning("Нет активных ботов в базе")
        return

    for enterprise in enterprises:
        token = enterprise.get("bot_token")
        number = enterprise.get("number")
        if not token or not number:
            logger.warning(f"Пропущена запись: {enterprise}")
            continue

        tasks.append(run_bot(token, number))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
