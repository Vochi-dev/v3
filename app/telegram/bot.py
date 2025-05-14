# app/telegram/bot.py
# -*- coding: utf-8 -*-
"""Точка входа бота (aiogram v3) — сохраняем enterprise_number."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import TELEGRAM_BOT_TOKEN
from app.services.db import get_enterprise_number_by_bot_token
from app.telegram.handlers.onboarding import router as onboarding_router

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())

    enterprise_number = await get_enterprise_number_by_bot_token(TELEGRAM_BOT_TOKEN)
    if enterprise_number is None:
        raise RuntimeError("bot_token не найден в таблице enterprises")

    bot["enterprise_number"] = enterprise_number
    logging.info("Bot started for enterprise %s", enterprise_number)

    dp.include_router(onboarding_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
