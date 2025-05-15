# app/routers/admin.py
# -*- coding: utf-8 -*-

import asyncio
import aiosqlite

from fastapi import (
    APIRouter, Request, Form, status, HTTPException
)
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.config import ADMIN_PASSWORD, DB_PATH
from app.services.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


def require_login(request: Request) -> None:
    if request.cookies.get("auth") != "1":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )


@router.get("", response_class=HTMLResponse)
async def root_redirect(request: Request):
    # если уже залогинен — на дашборд, иначе — на форму логина
    if request.cookies.get("auth") == "1":
        return RedirectResponse(
            url="/admin/dashboard",
            status_code=status.HTTP_302_FOUND
        )
    return RedirectResponse(
        url="/admin/login",
        status_code=status.HTTP_302_FOUND
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None}
    )


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    response = RedirectResponse(
        url="/admin/dashboard",
        status_code=status.HTTP_303_SEE_OTHER
    )
    response.set_cookie("auth", "1", httponly=True)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM enterprises")
        row = await cur.fetchone()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "enterprise_count": row["cnt"]}
    )


@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            "SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2 "
            "FROM enterprises ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": rows}
    )


# ───── Контроль сервисов и ботов ─────
@router.post("/service/stop")
async def service_stop(request: Request):
    require_login(request)
    # убиваем все uvicorn-процессы main:app
    proc = await asyncio.create_subprocess_shell(
        'pkill -f "uvicorn main:app"',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return PlainTextResponse("Service stopped")


@router.post("/service/start")
async def service_start(request: Request):
    require_login(request)
    # сначала останавливаем, потом запускаем в фоне
    await service_stop(request)
    proc = await asyncio.create_subprocess_shell(
        'nohup uvicorn main:app --host 0.0.0.0 --port 8001 >/dev/null 2>&1 &',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return PlainTextResponse("Service started")


@router.post("/bots/stop")
async def bots_stop(request: Request):
    require_login(request)
    # убиваем все процессы polling-бота
    proc = await asyncio.create_subprocess_shell(
        'pkill -f "python3 -m app.telegram.bot"',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return PlainTextResponse("All bots stopped")


@router.post("/bots/start")
async def bots_start(request: Request):
    require_login(request)
    # читаем все bot_token из enterprises и запускаем их polling’ом
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT bot_token FROM enterprises") as cur:
            rows = await cur.fetchall()
    for (token,) in rows:
        cmd = (
            f'export TELEGRAM_BOT_TOKEN="{token}" '
            f'&& nohup python3 -m app.telegram.bot >/dev/null 2>&1 &'
        )
        await asyncio.create_subprocess_shell(cmd)
    return PlainTextResponse("All bots started")
