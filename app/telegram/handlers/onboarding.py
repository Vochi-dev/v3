# app/telegram/handlers/onboarding.py
# -*- coding: utf-8 -*-
"""
Хэндлеры первого запуска бота:
• /start  → просим e-mail
• получаем e-mail → валидируем, проверяем БД, шлём письмо
(aiogram v3)
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.services.email_verification import (
    random_token,
    upsert_telegram_user,
    email_exists_for_enterprise,
    email_already_linked_to_another_bot,
    send_verification_email,
)

router = Router(name="onboarding")


class Signup(StatesGroup):
    waiting_email = State()


# ───────── /start ─────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Привет! Чтобы продолжить, отправьте корпоративный e-mail:"
    )
    await state.set_state(Signup.waiting_email)


# ───────── приём e-mail ───────────────────────────────────────────────
@router.message(Signup.waiting_email)
async def receive_email(message: Message, state: FSMContext) -> None:
    email = message.text.strip().lower()
    enterprise_id = message.bot["enterprise_id"]

    # 1. простой формат
    if "@" not in email or "." not in email:
        await message.answer("Похоже, это не e-mail. Попробуйте ещё раз:")
        return

    # 2. e-mail принадлежит предприятию?
    if not await email_exists_for_enterprise(email, enterprise_id):
        await message.answer(
            "⛔️ Этот e-mail не найден для вашего предприятия.\n"
            "Обратитесь к администратору."
        )
        await state.clear()
        return

    # 3. не активирован ли в другом боте?
    if await email_already_linked_to_another_bot(email, enterprise_id):
        await message.answer(
            "⛔️ Этот e-mail уже подтверждён в другом корпоративном боте."
        )
        await state.clear()
        return

    # 4. всё ок ― сохраняем и шлём письмо
    token = random_token()
    await upsert_telegram_user(
        telegram_id=message.from_user.id,
        enterprise_id=enterprise_id,
        email=email,
        token=token,
    )

    try:
        await send_verification_email(email, token)
        await message.answer(
            "✅ Письмо с подтверждением отправлено.\n"
            "Проверьте почту и перейдите по ссылке, чтобы закончить подключение."
        )
    except Exception:
        await message.answer(
            "⚠️ Не удалось отправить письмо. Попробуйте чуть позже."
        )

    await state.clear()
