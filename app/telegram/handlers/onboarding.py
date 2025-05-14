# app/telegram/handlers/onboarding.py
# -*- coding: utf-8 -*-
"""
Запрос e-mail и отправка письма — адаптировано под фактическую БД.
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.config import TELEGRAM_BOT_TOKEN
from app.services.db import get_enterprise_number_by_bot_token
from app.services.email_verification import (
    random_token,
    upsert_telegram_user,
    email_exists_for_enterprise,
    email_already_linked,
    send_verification_email,
)

router = Router(name="onboarding")


class Signup(StatesGroup):
    waiting_email = State()


# ---------------- /start --------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await message.answer("Привет! Введите ваш корпоративный e-mail:")
    await state.set_state(Signup.waiting_email)


# ---------------- приём e-mail --------------------------------------
@router.message(Signup.waiting_email)
async def receive_email(message: Message, state: FSMContext) -> None:
    email = message.text.strip().lower()

    enterprise_number = await get_enterprise_number_by_bot_token(
        TELEGRAM_BOT_TOKEN
    )
    if enterprise_number is None:
        await message.answer("⚠️ Конфигурация бота некорректна. Сообщите админу.")
        await state.clear()
        return

    # 1) быстрый формат-чек
    if "@" not in email or "." not in email:
        await message.answer("Это не похоже на e-mail. Попробуйте ещё раз:")
        return

    # 2) e-mail принадлежит предприятию?
    if not await email_exists_for_enterprise(email, enterprise_number):
        await message.answer(
            "⛔️ Такой e-mail не найден для вашего предприятия.\n"
            "Обратитесь к администратору."
        )
        await state.clear()
        return

    # 3) уже активирован в другом боте?
    if await email_already_linked(email):
        await message.answer("⛔️ Этот e-mail уже активирован в другом боте.")
        await state.clear()
        return

    # 4) всё ок ― генерируем токен, сохраняем, шлём письмо
    token = random_token()
    await upsert_telegram_user(message.from_user.id, email, token)

    try:
        await send_verification_email(email, token)
        await message.answer(
            "✅ Письмо отправлено! Проверьте почту и перейдите по ссылке."
        )
    except Exception:
        await message.answer("⚠️ Не удалось отправить письмо. Попробуйте позже.")

    await state.clear()
