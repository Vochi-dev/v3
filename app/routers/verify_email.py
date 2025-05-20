# app/routers/verify_email.py
# -*- coding: utf-8 -*-

import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.services.email_verification import verify_token_and_register_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(request: Request, token: str):
    try:
        await verify_token_and_register_user(token)
        return HTMLResponse(
            "<h2>Спасибо! Доступ подтверждён, вы успешно вошли в бота.</h2>", status_code=200
        )
    except Exception as e:
        logger.warning(f"Verification failed: {e}")
        raise HTTPException(400, "Неверная или просроченная ссылка.")
