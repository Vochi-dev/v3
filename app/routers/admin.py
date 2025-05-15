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
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("admin")


def require_login(request: Request) -> None:
    if request.cookies.get("auth") != "1":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )


@router.get("", response_class=HTMLResponse)
async def root_redirect(request: Request):
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


async def _broadcast(message: str) -> None:
    """
    Рассылает message в chat_id каждого предприятия через его bot_token,
    с детальным логированием.
    """
    logger.debug("Broadcast start: %r", message)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT bot_token, chat_id FROM enterprises") as cur:
            rows = await cur.fetchall()

    for row in rows:
        token = row["bot_token"]
        chat_id = row["chat_id"]
        logger.debug("Broadcast to enterprise: token=%s chat_id=%s", token, chat_id)
        if not token or not chat_id:
            logger.debug("  → skipped (missing token or chat_id)")
            continue

        bot = Bot(token=token)
        try:
            await bot.send_message(chat_id=chat_id, text=message)
            logger.debug("  → sent successfully to %s", chat_id)
        except TelegramError as e:
            logger.error("  → failed to send to %s: %s", chat_id, e)


# ───── Контроль сервисов и ботов ─────
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


@router.post("/service/start")
async def service_start(request: Request):
    require_login(request)
    logger.info("Admin requested service start")
    # остановим текущий процесс
    await service_stop(request)
    # и запустим новый
    proc = await asyncio.create_subprocess_shell(
        'nohup uvicorn main:app --host 0.0.0.0 --port 8001 >/dev/null 2>&1 &',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    logger.info("Service process started")
    await _broadcast("✅ Сервис активен")
    return PlainTextResponse("Service started")


@router.post("/bots/stop")
async def bots_stop(request: Request):
    require_login(request)
    logger.info("Admin requested bots stop")
    await _broadcast("⚠️ Боты отключаются — сервис деактивирован")
    proc = await asyncio.create_subprocess_shell(
        'pkill -f "python3 -m app.telegram.bot"',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    logger.info("All bot processes killed")
    return PlainTextResponse("All bots stopped")


@router.post("/bots/start")
async def bots_start(request: Request):
    require_login(request)
    logger.info("Admin requested bots start")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT bot_token FROM enterprises") as cur:
            rows = await cur.fetchall()

    for (token,) in rows:
        logger.debug("Starting bot process for token=%s", token)
        cmd = (
            f'export TELEGRAM_BOT_TOKEN="{token}" '
            f'&& nohup python3 -m app.telegram.bot >/dev/null 2>&1 &'
        )
        await asyncio.create_subprocess_shell(cmd)
    logger.info("All bot processes started")
    await _broadcast("✅ Сервис активен")
    return PlainTextResponse("All bots started")
