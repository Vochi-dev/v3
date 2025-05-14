# app/telegram/bot.py
import logging, asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from app.services.email_verification import (
    _random_token, upsert_telegram_user, email_exists,
    email_already_linked, send_verification_email,
)
from app.config import TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot, storage=MemoryStorage())

class AskEmail(StatesGroup):
    waiting_for_email = State()

# ───────── /start ──────────────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    await msg.answer("Введите корпоративный e-mail для подключения:")
    await AskEmail.waiting_for_email.set()

# ───────── приём e-mail ────────────────────────────────────────────────
@dp.message_handler(state=AskEmail.waiting_for_email)
async def process_email(msg: types.Message, state: FSMContext):
    email = msg.text.strip().lower()

    # 1. есть ли такой e-mail в белом списке?
    if not email_exists(email):
        await msg.answer("⛔️ Этот e-mail не найден в базе. Обратитесь к администратору.")
        await state.finish()
        return

    # 2. не привязан ли он к другому боту?
    if email_already_linked(email, TELEGRAM_BOT_TOKEN):
        await msg.answer("⛔️ Этот e-mail уже активирован в другом боте.")
        await state.finish()
        return

    # 3. создаём/обновляем запись и шлём письмо
    token = _random_token()
    upsert_telegram_user(msg.from_user.id, email, token)
    try:
        send_verification_email(email, token)
        await msg.answer(
            "✅ Мы отправили письмо с ссылкой подтверждения.\n"
            "Проверьте почту и кликните по ссылке, чтобы закончить подключение."
        )
    except Exception as e:
        logging.exception(e)
        await msg.answer("⚠️ Не удалось отправить письмо. Попробуйте позже.")
    finally:
        await state.finish()
