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

# Настройка логирования
logger = logging.getLogger("admin")

def require_login(request: Request) -> None:
    if request.cookies.get("auth") != "1":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

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
        return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный пароль"}, status_code=status.HTTP_401_UNAUTHORIZED)
    resp = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("auth", "1", httponly=True)
    return resp

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_login(request)
    db = await get_connection()
    cur = await db.execute("SELECT COUNT(*) AS cnt FROM enterprises")
    row = await cur.fetchone()
    await db.close()
    return templates.TemplateResponse("dashboard.html", {"request": request, "enterprise_count": row["cnt"]})

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
    return templates.TemplateResponse("enterprises.html", {"request": request, "enterprises": rows})

# ← НОВЫЙ МАРШРУТ ДЛЯ email-users
@router.get("/email-users", response_class=HTMLResponse)
async def email_users(request: Request):
    """
    Список всех пользователей, которым отправляется почтовая рассылка.
    Подставьте в шаблон нужные поля.
    """
    require_login(request)
    db = await get_connection()
    cur = await db.execute("SELECT id, email, verified, created_at FROM email_users ORDER BY created_at DESC")
    rows = await cur.fetchall()
    await db.close()
    return templates.TemplateResponse("email_users.html", {"request": request, "email_users": rows})

# остальной код без изменений...
