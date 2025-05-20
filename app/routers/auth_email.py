# app/routers/auth_email.py
# -*- coding: utf-8 -*-

import logging
import datetime

import aiosqlite
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.email_verification import verify_token_and_register_user
from app.services.enterprise import send_message_to_bot
from app.config import DB_PATH, settings

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("auth_email")
logger.setLevel(logging.DEBUG)


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(request: Request, token: str | None = None):
    """
    Обработчик перехода по ссылке из письма.
    URL: /verify-email?token=...
    """

    if not token:
        return templates.TemplateResponse(
            "verify_result.html",
            {
                "request": request,
                "success": False,
                "message": "Отсутствует токен подтверждения."
            },
            status_code=400
        )

    # Сначала прочитаем из email_tokens tg_id и bot_token (они ещё не удалены)
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute(
            "SELECT tg_id, bot_token, created_at FROM email_tokens WHERE token = ?",
            (token,)
        ).fetchone()

    if not row:
        logger.warning(f"Токен не найден при попытке верификации: {token}")
        return templates.TemplateResponse(
            "verify_result.html",
            {
                "request": request,
                "success": False,
                "message": "Неверный или устаревший токен."
            },
            status_code=400
        )

    # Проверим срок жизни токена
    created = datetime.datetime.fromisoformat(row["created_at"])
    if datetime.datetime.utcnow() - created > datetime.timedelta(hours=24):
        logger.warning(f"Токен устарел: {token}")
        return templates.TemplateResponse(
            "verify_result.html",
            {
                "request": request,
                "success": False,
                "message": "Токен истёк (более 24 часов). Пожалуйста, заново запросите подтверждение."
            },
            status_code=400
        )

    # Теперь попытаемся применить подтверждение и зарегистрировать пользователя
    try:
        await verify_token_and_register_user(token)
        logger.info(f"Email подтверждён, токен={token}")
    except RuntimeError as e:
        logger.warning(f"Не удалось подтвердить токен {token}: {e}")
        return templates.TemplateResponse(
            "verify_result.html",
            {
                "request": request,
                "success": False,
                "message": str(e)
            },
            status_code=400
        )
    except Exception as e:
        logger.exception(f"Ошибка при подтверждении токена {token}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Внутренняя ошибка сервера при подтверждении email."
        )

    # Всё успешно: отправляем пользователю в бот финальное сообщение
    tg_id = row["tg_id"]
    bot_token = row["bot_token"]
    try:
        sent = await send_message_to_bot(
            bot_token,
            tg_id,
            "🎉 Почта подтверждена! Бот полностью готов к работе."
        )
        if sent:
            logger.info(f"Отправлено завершающее сообщение пользователю {tg_id}")
        else:
            logger.warning(f"Не удалось отправить завершающее сообщение пользователю {tg_id}")
    except Exception as e:
        logger.exception(f"Ошибка при отправке завершающего сообщения {tg_id}: {e}")

    # Рендерим страницу успеха
    return templates.TemplateResponse(
        "verify_result.html",
        {
            "request": request,
            "success": True,
            "message": "Спасибо! Ваш e-mail подтверждён, доступ к боту активирован."
        }
    )
