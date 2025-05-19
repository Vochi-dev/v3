# app/telegram/dispatcher.py
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.services.db import get_enterprise_number_by_bot_token
from app.telegram.handlers.onboarding import create_onboarding_router

logger = logging.getLogger(__name__)


async def create_dispatcher(bot: Bot) -> Dispatcher:
    """
    Создаёт Dispatcher, привязывает его к конкретному предприятию
    (по bot_token → enterprises.bot_token в БД) и
    подключает все нужные роутеры.
    """
    dp = Dispatcher(storage=MemoryStorage())

    # --- узнаём, какому enterprise принадлежит этот бот ---
    enterprise_id = await get_enterprise_number_by_bot_token(bot.token)
    if enterprise_id is None:
        raise RuntimeError(
            "Этого bot_token нет в таблице enterprises – "
            "добавьте запись перед запуском."
        )

    # ✅ Сохраняем enterprise_id как атрибут бота
    bot.enterprise_id = enterprise_id

    # --- регистрируем новый Router для онбординга ---
    dp.include_router(create_onboarding_router())

    return dp


# --- добавлено для совместимости с main.py ---
async def setup_dispatcher(bot: Bot, enterprise_number: str) -> Dispatcher:
    """
    Совместимая обёртка setup_dispatcher для main.py
    """
    dp = Dispatcher(storage=MemoryStorage())

    # ✅ Сохраняем enterprise_id как атрибут бота
    bot.enterprise_id = enterprise_number

    # --- регистрируем новый Router для онбординга ---
    dp.include_router(create_onboarding_router())

    return dp
