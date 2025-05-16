# app/routers/admin.py
# -*- coding: utf-8 -*-

import asyncio
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
    # добавляем колонку active
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


# ─────── Форма добавления нового предприятия ───────
@router.get("/enterprises/add", response_class=HTMLResponse)
async def add_enterprise_form(request: Request):
    require_login(request)
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "add", "enterprise": {}}
    )


@router.post("/enterprises/add", response_class=HTMLResponse)
async def add_enterprise(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    require_login(request)
    created_at = datetime.utcnow().isoformat()
    db = await get_connection()
    try:
        await db.execute(
            "INSERT INTO enterprises(number, name, bot_token, chat_id, ip, secret, host, created_at, name2, active) "
            "VALUES(?,?,?,?,?,?,?,?,?,1)",
            (number, name, bot_token, chat_id, ip, secret, host, created_at, name2)
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


# ─────── Форма редактирования существующего предприятия ───────
@router.get("/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_form(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    cur = await db.execute(
        "SELECT number, name, bot_token, chat_id, ip, secret, host, name2, active "
        "FROM enterprises WHERE number = ?", (number,)
    )
    ent = await cur.fetchone()
    await db.close()
    if not ent:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "edit", "enterprise": dict(ent)}
    )


@router.post("/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise(
    request: Request,
    number: str,
    name: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
    active: int = Form(1),
):
    require_login(request)
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE enterprises SET name=?, bot_token=?, chat_id=?, ip=?, secret=?, host=?, name2=?, active=? "
            "WHERE number=?",
            (name, bot_token, chat_id, ip, secret, host, name2, active, number)
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


# ─────── AJAX: переключить статус бота ───────
@router.post("/enterprises/{number}/toggle", response_class=JSONResponse)
async def toggle_bot(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    cur = await db.execute("SELECT active, bot_token, chat_id FROM enterprises WHERE number = ?", (number,))
    ent = await cur.fetchone()
    if not ent:
        await db.close()
        raise HTTPException(status_code=404, detail="Enterprise not found")
    new_active = 0 if ent["active"] else 1
    await db.execute("UPDATE enterprises SET active=? WHERE number=?", (new_active, number))
    await db.commit()
    await db.close()

    # уведомляем сам корпоративный бот
    bot = Bot(token=ent["bot_token"])
    chat_id = int(ent["chat_id"])
    text = "✅ Сервис активирован" if new_active else "⚠️ Сервис деактивирован"
    try:
        bot.send_message(chat_id, text)
    except TelegramError as e:
        logger.error(f"Не удалось отправить уведомление в бот {number}: {e}")

    return {"active": new_active}


# ─────── Прочие маршруты … ───────
# (остальной код без изменений)
