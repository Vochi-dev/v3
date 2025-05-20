# app/routers/email_users.py
# -*- coding: utf-8 -*-

import logging

import aiosqlite
from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.enterprise import send_message_to_bot

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

logger = logging.getLogger("email_users")
logger.setLevel(logging.DEBUG)


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    require_login(request)
    # ... здесь ваш код списка пользователей без изменений ...
    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": []}  # ваш контекст
    )


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    # Ищем bot_token прямо в telegram_users
    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,)
        )
        row = await cur.fetchone()

    if row and row["bot_token"]:
        bot_token = row["bot_token"]
        logger.info(f"[delete] Найден bot_token={bot_token} для tg_id={tg_id}")
    else:
        logger.error(f"[delete] bot_token не найден для tg_id={tg_id}, сообщение не отправлено")
        return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    # Отправляем **единственное** фиксированное сообщение
    try:
        sent = await send_message_to_bot(
            bot_token,
            tg_id,
            "❌ Ваш доступ к боту был отозван администратором."
        )
        if sent:
            logger.info(f"[delete] Уведомление отправлено пользователю {tg_id}")
        else:
            logger.warning(f"[delete] Не удалось отправить уведомление пользователю {tg_id}")
    except Exception as e:
        logger.error(f"[delete] Ошибка при отправке уведомления пользователю {tg_id}: {e}")

    # **Больше ничего не делаем** — не трогаем базу
    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)
