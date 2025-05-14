# app/telegram/bot.py
# -*- coding: utf-8 -*-
"""
Telegram-бот (aiogram v3.20+):
— создаём global bot и dp
— функция setup_bot для инициализации (сброс webhook,
  определение enterprise, регистрация хэндлеров)
— main для standalone запуска
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import TELEGRAM_BOT_TOKEN
from app.services.db import get_enterprise_number_by_bot_token
from app.telegram.handlers.onboarding import router as onboarding_router

logging.basicConfig(level=logging.INFO)

# глобальные объекты
bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher(storage=MemoryStorage())


async def setup_bot() -> None:
    """
    Сброс webhook → определение enterprise_number → регистрация роутеров
    """
    # 1) switch to long-polling
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook removed, switched to long-polling")

    # 2) fetch enterprise_number
    enterprise_number = await get_enterprise_number_by_bot_token(
        TELEGRAM_BOT_TOKEN
    )
    if enterprise_number is None:
        raise RuntimeError("bot_token not found in enterprises table")
    bot["enterprise_number"] = enterprise_number
    logging.info("Bot started for enterprise %s", enterprise_number)

    # 3) register handlers
    dp.include_router(onboarding_router)


async def main() -> None:
    """Stand-alone entrypoint."""
    await setup_bot()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
