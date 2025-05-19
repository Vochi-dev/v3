# app/telegram/dispatcher.py
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings               # BOT_TOKEN и путь к БД храним в settings
from app.services.db import get_enterprise_by_bot_token  # маленькая helper-функция
from app.telegram import onboarding  # исправлено — теперь путь корректный


async def create_dispatcher() -> Dispatcher:
    """
    Создаёт Dispatcher, привязывает его к конкретному предприятию
    (по bot_token → enterprises.bot_token в БД) и
    подключает все нужные роутеры.
    """
    bot = Bot(token=settings.BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())

    # --- узнаём, какому enterprise принадлежит этот бот ---
    enterprise = await get_enterprise_by_bot_token(settings.BOT_TOKEN)
    if enterprise is None:
        raise RuntimeError(
            "Этого bot_token нет в таблице enterprises – "
            "добавьте запись перед запуском."
        )

    # Сохраняем id предприятия в контексте бота → доступно во всех хэндлерах:
    # message.bot['enterprise_id']
    bot["enterprise_id"] = enterprise.id

    # --- регистрируем наши хэндлеры ---
    dp.include_router(onboarding.router)

    return dp
