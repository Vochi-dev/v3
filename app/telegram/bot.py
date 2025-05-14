import asyncio, secrets, sqlite3
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode

from app.config import TELEGRAM_BOT_TOKEN, DB_PATH, EMAIL_FROM
from app.services.mailer import send_email  # создадим ниже tiny helper

bot = Bot(token=TELEGRAM_BOT_TOKEN, parse_mode=ParseMode.HTML)
dp  = Dispatcher()
r   = Router()
dp.include_router(r)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@r.message(CommandStart())
async def ask_email(msg: types.Message):
    await msg.answer(
        "Привет! Чтобы зарегистрироваться, пришлите свой рабочий e-mail."
    )


@r.message()
async def handle_email(msg: types.Message):
    email = msg.text.strip().lower()
    if "@" not in email:
        return await msg.reply("Похоже, это не e-mail. Попробуйте ещё раз.")

    conn = get_db()
    cur  = conn.cursor()

    # есть ли такой email в справочнике?
    cur.execute("SELECT 1 FROM email_users WHERE email = ?", (email,))
    if not cur.fetchone():
        conn.close()
        return await msg.reply("Такого e-mail нет в базе. Обратитесь к администратору.")

    # генерируем/обновляем токен
    token = secrets.token_urlsafe(24)
    cur.execute("""
        INSERT INTO telegram_users (tg_id, email, token, verified)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(tg_id) DO UPDATE SET email = excluded.email,
                                         token = excluded.token,
                                         verified = 0
    """, (msg.from_user.id, email, token))
    conn.commit()
    conn.close()

    # шлём письмо
    verify_link = f"https://bot.vochi.by:8001/verify-email/{token}"
    await asyncio.to_thread(
        send_email,
        to=email,
        subject="Подтверждение регистрации",
        text=f"Нажмите для подтверждения: {verify_link}\n\nЕсли это были не вы — проигнорируйте."
    )

    await msg.answer("Мы отправили письмо на ваш e-mail.\n"
                     "Перейдите по ссылке, чтобы завершить регистрацию ✉️")
