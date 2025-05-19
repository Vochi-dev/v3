# app/telegram/dispatcher.py
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode

from app.telegram.handlers import onboarding  # router с логикой start/email
from app.services.db import get_enterprise_by_token  # достаёт данные по токену


async def setup_dispatcher(bot: Bot, enterprise_number: str) -> Dispatcher:
    """
    Создаёт Dispatcher для конкретного Telegram-бота,
    подключает router onboarding с логикой авторизации.
    """
    dp = Dispatcher(storage=MemoryStorage())

    # Вписываем enterprise_number в словарь бота — доступен в хендлерах
    bot["enterprise_number"] = enterprise_number

    # Подключаем роутеры
    dp.include_router(onboarding.router)

    return dp
