# app/telegram/bot.py
# -*- coding: utf-8 -*-
"""Точка входа aiogram-бота (v3.20+) с автосбросом webhook."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import TELEGRAM_BOT_TOKEN
from app.services.db import get_enterprise_number_by_bot_token
from app.telegram.handlers.onboarding import router as onboarding_router

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    # ---- инициализируем бота ---------------------------------------
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # ---- убираем возможный старый webhook --------------------------
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook removed, switched to long-polling")

    # ---- просто логируем, что это за предприятие -------------------
    enterprise_number = await get_enterprise_number_by_bot_token(
        TELEGRAM_BOT_TOKEN
    )
    if enterprise_number is None:
        raise RuntimeError("bot_token не найден в таблице enterprises")

    logging.info("Bot started for enterprise %s", enterprise_number)

    # ---- подключаем хэндлеры и запускаем ---------------------------
    dp.include_router(onboarding_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
