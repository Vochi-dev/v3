# app/routers/admin.py
# -*- coding: utf-8 -*-

import asyncio
import aiosqlite
import logging

from fastapi import (
    APIRouter, Request, Form, status, HTTPException
)
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )


# ─────── Авторизация ───────
@router.get("", response_class=HTMLResponse)
async def root_redirect(request: Request):
    if request.cookies.get("auth") == "1":
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    resp = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("auth", "1", httponly=True)
    return resp


# ─────── Дашборд ───────
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_login(request)
    db = await get_connection()
    cur = await db.execute("SELECT COUNT(*) AS cnt FROM enterprises")
    row = await cur.fetchone()
    await db.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "enterprise_count": row["cnt"]}
    )


# ─────── Список предприятий ───────
@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    cur = await db.execute(
        "SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2 "
        "FROM enterprises ORDER BY created_at DESC"
    )
    rows = await cur.fetchall()
    await db.close()
    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": rows}
    )


# ─────── Список e-mail пользователей ───────
@router.get("/email-users", response_class=HTMLResponse)
async def email_users(request: Request):
    """
    Список всех e-mail пользователей. 
    Для шаблона email_users.html нужны поля:
      tg_id, email, name, right_all, right_1, right_2, enterprise_name
    Столбец telegram_id в БД приводим к tg_id.
    """
    require_login(request)
    db = await get_connection()
    cur = await db.execute("""
        SELECT
            telegram_id   AS tg_id,
            email,
            name,
            right_all,
            right_1,
            right_2,
            enterprise_name
        FROM email_users
        ORDER BY created_at DESC
    """)
    rows = await cur.fetchall()
    await db.close()
    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows}
    )


# ─────── Вспомогательная рассылка ───────
async def _broadcast(message: str) -> None:
    logger.debug("Broadcast start: %r", message)

    # 1) Администраторы предприятий
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT bot_token, chat_id FROM enterprises") as cur:
            ent_rows = await cur.fetchall()
        # 2) Все верифицированные telegram-пользователи
        async with db.execute(
            "SELECT tg_id, bot_token FROM telegram_users WHERE verified = 1"
        ) as cur2:
            user_rows = await cur2.fetchall()

    for ent in ent_rows:
        token, chat_id = ent["bot_token"], ent["chat_id"]
        if token and chat_id:
            bot = Bot(token=token)
            try:
                await bot.send_message(chat_id=chat_id, text=message)
            except TelegramError:
                logger.exception("Failed to send to enterprise %s", chat_id)

    for usr in user_rows:
        tg_id, token = usr["tg_id"], usr["bot_token"]
        if token and tg_id:
            bot = Bot(token=token)
            try:
                await bot.send_message(chat_id=tg_id, text=message)
            except TelegramError:
                logger.exception("Failed to send to user %s", tg_id)


# ─────── Контроль сервисов и ботов ───────
@router.post("/service/stop")
async def service_stop(request: Request):
    require_login(request)
    logger.info("Admin requested service stop")
    await _broadcast("⚠️ Сервис деактивирован")
    proc = await asyncio.create_subprocess_shell(
        'pkill -f "uvicorn main:app"',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    logger.info("Service processes killed")
    return PlainTextResponse("Service stopped")

# здесь далее — остальные маршруты service/start, bots/stop, bots/start без изменений
