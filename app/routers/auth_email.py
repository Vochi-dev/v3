# app/routers/auth_email.py
# -*- coding: utf-8 -*-

import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.email_verification import verify_token_and_register_user

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
        # Токен отсутствует
        return templates.TemplateResponse(
            "verify_result.html",
            {
                "request": request,
                "success": False,
                "message": "Отсутствует токен подтверждения."
            },
            status_code=400
        )

    try:
        # Попытка проверить токен и зарегистрировать пользователя
        await verify_token_and_register_user(token)
        logger.info(f"Email подтверждён, токен={token}")
    except RuntimeError as e:
        # Неправильный или просроченный токен
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
        # Любая другая ошибка
        logger.exception(f"Ошибка при подтверждении токена {token}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Внутренняя ошибка сервера при подтверждении e-mail."
        )

    # Успех
    return templates.TemplateResponse(
        "verify_result.html",
        {
            "request": request,
            "success": True,
            "message": "Спасибо! Ваш e-mail подтверждён, доступ к боту активирован."
        }
    )
