# app/telegram/dispatcher.py
# -*- coding: utf-8 -*-
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.services.database import get_enterprise_number_by_bot_token
from app.telegram.onboarding import create_onboarding_router

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

    # --- регистрируем Router онбординга ---
    dp.include_router(create_onboarding_router())
    
    # --- регистрируем Router авторизации ---
    try:
        # Импортируем наш auth handler
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from telegram_auth_handler import create_auth_router
        dp.include_router(create_auth_router())
        logger.info(f"Auth router добавлен для предприятия {enterprise_id}")
    except Exception as e:
        logger.warning(f"Не удалось загрузить auth router: {e}")

    return dp


# --- добавлено для совместимости с main.py ---
async def setup_dispatcher(bot: Bot, enterprise_number: str = None) -> Dispatcher:
    """
    Совместимая обёртка setup_dispatcher для main.py
    """
    dp = Dispatcher(storage=MemoryStorage())

    # ✅ Сохраняем enterprise_id как атрибут бота
    bot.enterprise_id = enterprise_number

    # --- регистрируем Router онбординга ---
    dp.include_router(create_onboarding_router())

    return dp
