# app/routers/auth_email.py
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
import aiosqlite

from app.services.email_verification import mark_verified
from app.config import DB_PATH
from aiogram import Bot as AiogramBot

router = APIRouter()


@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email(token: str):
    ok, tg_id = await mark_verified(token)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Токен не найден или устарел")

    # получаем bot_token из telegram_users
    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,)
        )
        row = await cur.fetchone()
        if row:
            bot_token = row["bot_token"]

    if not bot_token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Не удалось определить бот для уведомления")

    bot = AiogramBot(token=bot_token)
    try:
        await bot.send_message(
            chat_id=tg_id,
            text="🎉 Почта подтверждена! Бот полностью готов к работе."
        )
    except:
        pass

    return """
    <h1>Почта подтверждена!</h1>
    <p>Теперь вы можете вернуться в Telegram-бот и пользоваться всеми функциями.</p>
    """
