# app/telegram/onboarding.py
# -*- coding: utf-8 -*-

import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.services.email_verification import (
    create_verification_token,
    upsert_telegram_user,
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
        user_id = message.from_user.id
        bot_token = message.bot.token
        logger.info(f"Получен e-mail от {user_id}: {email}")

        # 1) Простая валидация
        if "@" not in email or "." not in email:
            logger.warning(f"Невалидный email от {user_id}: {email}")
            await message.answer("Это не похоже на e-mail. Попробуйте ещё раз:")
            return

        # 2) Есть ли такой e-mail в разрешённом списке?
        if not await email_exists(email):
            logger.warning(f"Не найден email в базе: {email}")
            await message.answer("⛔️ Такой e-mail не найден. Обратитесь к администратору.")
            await state.clear()
            return

        # 3) Не был ли он уже подтверждён в другом боте?
        if await email_already_verified(email):
            logger.warning(f"Email уже подтверждён ранее: {email}")
            await message.answer("⛔️ Этот e-mail уже подтверждён в другом боте.")
            await state.clear()
            return

        # 4) Генерация токена
        token = create_verification_token(email)
        logger.debug(f"Сгенерирован токен для {email}: {token}")

        # 5) Сначала отправляем письмо. Если не получилось — выходим.
        try:
            send_verification_email(email, token)
            logger.info(f"Письмо с токеном отправлено на {email}")
        except Exception as e:
            logger.exception(f"Ошибка при отправке письма на {email}: {e}")
            await message.answer(
                "⚠️ Не удалось отправить письмо с ссылкой. "
                "Пожалуйста, попробуйте позже или свяжитесь с администратором."
            )
            # НЕ вызываем upsert_telegram_user, не сохраняем юзера
            return

        # 6) Если письмо ушло — сохраняем (или обновляем) запись в БД
        try:
            await upsert_telegram_user(
                user_id,
                email,
                token,
                bot_token
            )
            logger.debug(f"Telegram-пользователь {user_id} сохранён в БД")
        except Exception as e:
            # Это критическая ошибка, но письмо уже ушло => сообщаем админу, а пользователю даём знать
            logger.exception(f"Ошибка записи telegram_users для {user_id}: {e}")
            await message.answer(
                "⚠️ Ваша регистрация прошла не до конца из-за внутренней ошибки. "
                "Попробуйте ещё раз чуть позже."
            )
            return

        # 7) Уведомляем пользователя, что письмо ушло
        await message.answer(
            "✅ Письмо отправлено! Проверьте почту и перейдите по ссылке для подтверждения."
        )

        # 8) Сбрасываем состояние FSM
        await state.clear()
        logger.debug(f"Состояние очищено для пользователя {user_id}")

    return router
