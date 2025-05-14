"""
aiogram-бот регистрации сотрудников.

• /start  -> спрашиваем e-mail
• получаем e-mail -> проверяем в email_users
• создаём запись в telegram_users с token-uuid
• отправляем письмо с ссылкой /verify-email/{token}
"""
import uuid, asyncio, sqlite3, os
from aiogram import Bot, Dispatcher, types
from aiogram.types import ParseMode
from aiogram.utils import executor
from email.message import EmailMessage
import smtplib

from app.config import (
    DB_PATH, EMAIL_HOST, EMAIL_PORT,
    EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
    EMAIL_USE_TLS, EMAIL_FROM
)

API_TOKEN = os.getenv("AIORGRAM_TOKEN", "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA")  # тот же токен
bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ───────── helpers ─────────
def _send_mail(to_mail: str, verify_link: str):
    msg = EmailMessage()
    msg["Subject"] = "Подтверждение e-mail для сервиса звонков"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = to_mail
    msg.set_content(
        f"Здравствуйте!\nЧтобы завершить регистрацию, перейдите по ссылке:\n{verify_link}\n"
    )

    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as s:
        if EMAIL_USE_TLS:
            s.starttls()
        s.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
        s.send_message(msg)

# ───────── handlers ─────────
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    await msg.answer("Введите ваш рабочий e-mail:")

@dp.message_handler(lambda m: "@" in m.text)
async def handle_email(msg: types.Message):
    email = msg.text.strip().lower()

    conn = _db(); cur = conn.cursor()
    # есть ли такой email в справочнике?
    cur.execute("SELECT * FROM email_users WHERE email = ?", (email,))
    if not cur.fetchone():
        await msg.answer("❌ Этот e-mail не найден в списке сотрудников.\n"
                         "Попробуйте ещё раз или обратитесь к администратору.")
        return

    # создаём / обновляем telegram_users
    token = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO telegram_users (tg_id, email, token, verified)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(tg_id) DO UPDATE SET email=excluded.email, token=excluded.token, verified=0
    """, (msg.from_user.id, email, token))
    conn.commit(); conn.close()

    link = f"https://bot.vochi.by:8001/verify-email/{token}"
    # письмо
    try:
        _send_mail(email, link)
    except Exception as e:
        await msg.answer("⚠️ Не удалось отправить письмо. Сообщите администратору.")
        raise

    await msg.answer("✅ Ссылка для подтверждения отправлена на почту.\n"
                     "Проверьте e-mail и перейдите по ссылке, "
                     "затем вернитесь в бот.")

# ───────── run via executor (для локального запуска) ─────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
