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
    """
    Создаёт и возвращает новый Router со всеми хэндлерами
    для онбординга пользователя.
    """
    router = Router(name="onboarding")

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        logger.info(
            f"/start от пользователя: {message.from_user.id} "
            f"({message.from_user.username})"
        )
        await message.answer("Привет! Введите ваш корпоративный e-mail:")
        await state.set_state(Signup.waiting_email)
        logger.debug("Установлено состояние: waiting_email")

    @router.message(Signup.waiting_email)
    async def receive_email(message: Message, state: FSMContext) -> None:
        email = message.text.strip().lower()
        logger.info(f"Получен e-mail от {message.from_user.id}: {email}")

        # Простая валидация формата
        if "@" not in email or "." not in email:
            logger.warning(f"Невалидный email от {message.from_user.id}: {email}")
            await message.answer("Это не похоже на e-mail. Попробуйте ещё раз:")
            return

        # Проверка в БД: такой e-mail должен быть в таблице email_users
        if not await email_exists(email):
            logger.warning(f"Не найден email в базе: {email}")
            await message.answer("⛔️ Такой e-mail не найден. Обратитесь к администратору.")
            await state.clear()
            return

        # Проверка, не подтверждён ли уже
        if await email_already_verified(email):
            logger.warning(f"Email уже подтверждён ранее: {email}")
            await message.answer("⛔️ Этот e-mail уже подтверждён в другом боте.")
            await state.clear()
            return

        # Генерируем и сохраняем токен вместе с tg_id и bot_token
        token = create_and_store_token(
            email,
            message.from_user.id,
            message.bot.token
        )

        # Отправляем письмо с ссылкой на подтверждение
        try:
            send_verification_email(email, token)
            logger.info(f"Письмо отправлено: {email}, токен: {token}")
            await message.answer(
                "✅ Письмо отправлено! Проверьте почту и перейдите по ссылке."
            )
        except Exception as e:
            logger.exception(f"Ошибка при отправке письма на {email}: {e}")
            await message.answer("⚠️ Не удалось отправить письмо. Попробуйте позже.")

        # Очистка FSM (пользователь снова стартует командой /start после письма)
        await state.clear()
        logger.debug(f"Состояние очищено для пользователя {message.from_user.id}")

    return router
