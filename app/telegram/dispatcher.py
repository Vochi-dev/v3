# app/telegram/dispatcher.py
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.services.db import get_enterprise_number_by_bot_token
from app.telegram import onboarding


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

    # --- регистрируем наши хэндлеры ---
    # Сбрасываем привязку родителя, чтобы можно было многократно подключать
    onboarding.router.parent_router = None
    dp.include_router(onboarding.router)

    return dp


# --- добавлено для совместимости с main.py ---
async def setup_dispatcher(bot: Bot, enterprise_number: str) -> Dispatcher:
    """
    Совместимая обёртка setup_dispatcher для main.py
    """
    dp = Dispatcher(storage=MemoryStorage())

    # ✅ Сохраняем enterprise_id как атрибут бота
    bot.enterprise_id = enterprise_number

    # --- регистрируем наши хэндлеры ---
    onboarding.router.parent_router = None
    dp.include_router(onboarding.router)
    return dp
