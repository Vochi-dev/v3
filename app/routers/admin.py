# app/routers/admin.py
# -*- coding: utf-8 -*-
import aiosqlite, logging
from datetime import datetime

from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from telegram import Bot

from app.config import ADMIN_PASSWORD, DB_PATH, NOTIFY_BOT_TOKEN, NOTIFY_CHAT_ID
from app.services.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("admin")

def require_login(request: Request):
    if request.cookies.get("auth") != "1":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

# — Авторизация, /login и /dashboard остаются без изменений —

# ─────── Список предприятий ───────
@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    cur = await db.execute("""
        SELECT
          number, name, bot_token, active,
          chat_id, ip, secret, host, name2
        FROM enterprises
        ORDER BY created_at DESC
    """)
    rows = await cur.fetchall()
    await db.close()
    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": rows}
    )

# ─────── Переключение статуса ───────
@router.post("/enterprises/{number}/toggle", response_class=RedirectResponse)
async def toggle_enterprise(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    # получаем текущий статус
    cur = await db.execute(
        "SELECT active FROM enterprises WHERE number = ?", (number,)
    )
    row = await cur.fetchone()
    if not row:
        await db.close()
        raise HTTPException(status_code=404, detail="Enterprise not found")
    new_status = 0 if row["active"] else 1
    # обновляем
    await db.execute(
        "UPDATE enterprises SET active = ? WHERE number = ?",
        (new_status, number)
    )
    await db.commit()
    await db.close()

    # уведомляем notify-бота
    bot = Bot(token=NOTIFY_BOT_TOKEN)
    text = f"Предприятие {number} {'активировано' if new_status else 'деактивировано'}"
    try:
        await bot.send_message(chat_id=int(NOTIFY_CHAT_ID), text=text)
        logger.info("Notify-bot: %s", text)
    except Exception as e:
        logger.error("Notify-bot failed: %s", e)

    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

# — Остальные маршруты add/edit/… без изменений, только сюда вставить выше —
