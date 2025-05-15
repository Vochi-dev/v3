# app/routers/auth_email.py
# -*- coding: utf-8 -*-
"""
Эндпоинт подтверждения e-mail по токену из письма + уведомление
пользователя в Telegram. Работает с aiogram-ботами (v3) и FastAPI.
"""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from telegram import Bot                                # python-telegram-bot v20+
from app.config import TELEGRAM_BOT_TOKEN

# mark_verified – асинхронная функция!  ↓↓↓
from app.services.email_verification import mark_verified

router = APIRouter(tags=["auth_email"])
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# -------------------------------------------------------------------- #
@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email(token: str):
    """
    • Проверяем токен → ставим verified = 1 (функция mark_verified)  
    • Уведомляем пользователя в Telegram, если знаем его chat_id  
    • Показываем простую HTML-страницу об успехе / ошибке
    """
    ok, tg_id = await mark_verified(token)       # ← обязательно await!

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ссылка недействительна или устарела",
        )

    # Пытаемся отправить сообщение в Telegram — не критично, если не вышло
    if tg_id:
        try:
            await notify_bot.send_message(
                chat_id=tg_id,
                text="🎉 Почта подтверждена! Бот полностью готов к работе.",
            )
        except Exception:d
            pass

    return HTMLResponse(
        """
        <html>
          <head><title>Verification OK</title></head>
          <body style='font-family:sans-serif;text-align:center;margin-top:4rem'>
            <h2>✅ Почта подтверждена!</h2>
            <p>Можете вернуться в Telegram-бот.</p>
          </body>
        </html>
        """
    )
