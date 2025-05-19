# app/routers/auth_email.py
# -*- coding: utf-8 -*-

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
import aiosqlite

from aiogram import Bot as AiogramBot
from app.config import DB_PATH
from app.services.email_verification import mark_verified

router = APIRouter(prefix="/verify-email", tags=["auth"])


@router.get("/{token}", response_class=HTMLResponse)
async def verify_email(token: str):
    """
    Подтверждение e-mail по токену.
    - mark_verified возвращает (ok: bool, tg_id: int|None)
    - при успехе отправляем пользователю в боте уведомление и показываем HTML
    """
    ok, tg_id = await mark_verified(token)
    if not ok or tg_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Токен не найден или устарел"
        )

    # Получаем bot_token по tg_id
    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?",
            (tg_id,)
        )
        row = await cur.fetchone()
        if row:
            bot_token = row["bot_token"]

    if not bot_token:
        # Пользователь есть, но бот почему-то не привязан
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось определить бот для уведомления"
        )

    # Отправляем в Telegram
    try:
        bot = AiogramBot(token=bot_token)
        await bot.send_message(
            chat_id=tg_id,
            text="🎉 Ваш e-mail подтверждён! Бот полностью готов к работе."
        )
    except Exception:
        # Игнорируем ошибки отправки
        pass

    # Возвращаем простую HTML-страницу
    return """
    <!DOCTYPE html>
    <html lang="ru">
      <head>
        <meta charset="UTF-8">
        <title>Почта подтверждена</title>
        <style>
          body { font-family: sans-serif; padding: 2rem; text-align: center; }
          h1 { color: #28a745; }
          p  { font-size: 1.1rem; }
        </style>
      </head>
      <body>
        <h1>✅ Почта подтверждена!</h1>
        <p>Теперь вы можете вернуться в Telegram-бот и пользоваться всеми функциями.</p>
      </body>
    </html>
    """
