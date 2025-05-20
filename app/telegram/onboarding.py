# app/telegram/onboarding.py
# -*- coding: utf-8 -*-

import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.services.email_verification import (
    create_and_store_token,
    email_exists,
    email_already_verified,
    send_verification_email,
)

logger = logging.getLogger(__name__)


class Signup(StatesGroup):
    waiting_email = State()


def create_onboarding_router() -> Router:
    router = Router(name="onboarding")

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await message.answer("Привет! Введите ваш корпоративный e-mail:")
        await state.set_state(Signup.waiting_email)

    @router.message(Signup.waiting_email)
    async def receive_email(message: Message, state: FSMContext):
        email = message.text.strip().lower()
        tg_id = message.from_user.id
        bot_token = message.bot.token

        # Валидация
        if "@" not in email or "." not in email:
            await message.answer("Это не похоже на e-mail. Попробуйте ещё раз:")
            return

        if not await email_exists(email):
            await message.answer("⛔️ Такой e-mail не найден. Обратитесь к администратору.")
            await state.clear()
            return

        if await email_already_verified(email):
            await message.answer("⛔️ Этот e-mail уже подтверждён в другом боте.")
            await state.clear()
            return

        # Генерим и сохраняем токен вместе с tg_id и bot_token
        token = create_and_store_token(email, tg_id, bot_token)

        # Отправляем письмо
        try:
            send_verification_email(email, token)
            await message.answer("✅ Письмо отправлено! Проверьте почту и перейдите по ссылке.")
        except Exception:
            logger.exception("Ошибка при отправке письма")
            await message.answer("⚠️ Не удалось отправить письмо. Попробуйте позже.")

        await state.clear()

    return router
