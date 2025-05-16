# app/routers/admin.py
# -*- coding: utf-8 -*-

import aiosqlite
import logging
from datetime import datetime

from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telegram import Bot
from telegram.error import TelegramError

from app.config import ADMIN_PASSWORD, DB_PATH
from app.services.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("admin")


def require_login(request: Request) -> None:
    if request.cookies.get("auth") != "1":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# ... login и dashboard без изменений ...


# ─────── Список предприятий ───────
@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    cur = await db.execute(
        "SELECT number, name, bot_token, chat_id, ip, secret, host, name2, active "
        "FROM enterprises ORDER BY created_at DESC"
    )
    rows = await cur.fetchall()
    await db.close()
    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": rows}
    )


# ─────── Переключение статуса ───────
@router.post("/enterprises/{number}/toggle")
async def toggle_enterprise(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    # получаем текущее состояние и токены
    cur = await db.execute(
        "SELECT active, bot_token, chat_id FROM enterprises WHERE number = ?", (number,)
    )
    ent = await cur.fetchone()
    if not ent:
        await db.close()
        raise HTTPException(status_code=404, detail="Enterprise not found")
    new_active = 0 if ent["active"] else 1
    # обновляем базу
    await db.execute(
        "UPDATE enterprises SET active = ? WHERE number = ?",
        (new_active, number)
    )
    await db.commit()
    await db.close()
    # уведомляем соответствующий бот
    try:
        bot = Bot(token=ent["bot_token"])
        text = "🚫 Бот деактивирован" if new_active == 0 else "✅ Бот активирован"
        await bot.send_message(chat_id=int(ent["chat_id"]), text=text)
    except TelegramError as e:
        logger.error(f"Failed to notify enterprise bot: {e}")
    return JSONResponse({"active": bool(new_active)})


# ─────── Формы add/edit остаются без изменений ───────
# ... add_enterprise, edit_enterprise_form, edit_enterprise ...
