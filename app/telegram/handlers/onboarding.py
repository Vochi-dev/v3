# app/telegram/handlers/onboarding.py
# -*- coding: utf-8 -*-
import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.services.email_verification import (
    random_token,
    upsert_telegram_user,
    email_exists,
    email_already_verified,
    send_verification_email,
)

router = Router(name="onboarding")


class Signup(StatesGroup):
    waiting_email = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await message.answer("Привет! Введите ваш корпоративный e-mail:")
    await state.set_state(Signup.waiting_email)


@router.message(Signup.waiting_email)
async def receive_email(message: Message, state: FSMContext) -> None:
    email = message.text.strip().lower()
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

    token = random_token()
    # сохраняем также токен текущего бота:
    await upsert_telegram_user(
        message.from_user.id,
        email,
        token,
        message.bot.token
    )

    try:
        await send_verification_email(email, token)
        await message.answer("✅ Письмо отправлено! Проверьте почту и перейдите по ссылке.")
    except Exception:
        logging.exception("Error sending verification email to %s", email)
        await message.answer("⚠️ Не удалось отправить письмо. Попробуйте позже.")

    await state.clear()
