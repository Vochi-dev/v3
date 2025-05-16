# main.py
# -*- coding: utf-8 -*-
"""
FastAPI-приложение:
— /health
— /admin/...        админ-панель
— /events/...       вебхуки Asterisk
— /{event_type}     вебхуки без префикса /events (для совместимости)
Polling Telegram-бота запускается отдельно: python3 -m app.telegram.bot
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from telegram import Bot
import aiosqlite

from app.config import DB_PATH
from app.services.events import (
    init_database_tables,
    load_hangup_message_history,
    save_asterisk_event,
)
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup,
    create_resend_loop,
)
from app.services.calls.internal import (
    process_internal_start,
    process_internal_bridge,
    process_internal_hangup,
)
from app.services.calls.utils import is_internal_number

# подключаем админ-маршруты
from app.routers.admin import router as admin_router

# ───────── константы из окружения ─────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# ───────── логирование ─────────
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.DEBUG)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(console)
fileh = logging.FileHandler("asterisk_events.log")
fileh.setLevel(logging.INFO)
fileh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(fileh)

# ───────── FastAPI & шаблоны ─────────
app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

# ───────── подключаем админ-роутер под /admin ─────────
app.include_router(admin_router, prefix="/admin")

# ───────── middleware логирования ─────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = await request.body()
    logger.debug(f"Incoming request: {request.method} {request.url} — Body: {body!r}")
    return await call_next(request)

# ───────── уведомительный бот ─────────
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def broadcast_to_enterprises(text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT bot_token, chat_id FROM enterprises")).fetchall()
    for row in rows:
        bot = Bot(token=row["bot_token"])
        try:
            await bot.send_message(chat_id=int(row["chat_id"]), text=text)
        except Exception as e:
            logger.error(f"Enterprise broadcast failed to {row['chat_id']}: {e}")

async def broadcast_to_subscribers(text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT tg_id, bot_token FROM telegram_users WHERE verified = 1"
        )).fetchall()
    for row in rows:
        bot = Bot(token=row["bot_token"])
        try:
            await bot.send_message(chat_id=int(row["tg_id"]), text=text)
        except Exception as e:
            logger.error(f"Subscriber broadcast failed to {row['tg_id']}: {e}")

@app.on_event("startup")
async def startup_tasks():
    logger.debug("Startup: init DB & load history")
    await init_database_tables()
    await load_hangup_message_history()

    # уведомление об старте
    try:
        await notify_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="✅ Сервис запущен и готов к приёму событий."
        )
    except Exception as e:
        logger.error(f"Notify-бот: {e}")

    await broadcast_to_enterprises("✅ Сервис запущен и готов к приёму событий.")
    await broadcast_to_subscribers("✅ Сервис запущен и готов к приёму событий.")

    logger.debug("Starting resend loop")
    asyncio.create_task(create_resend_loop({}, {}, {}, notify_bot, TELEGRAM_CHAT_ID))

@app.get("/health")
async def health():
    return {"status": "ok"}

async def handle_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")

    await save_asterisk_event(et, uid, token, data)

    # найти bot_token
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        ent = await (await db.execute(
            "SELECT bot_token FROM enterprises WHERE name2 = ?", (token,)
        )).fetchone()
    if not ent:
        return {"status": "no_such_bot"}

    bot_token = ent["bot_token"]
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        users = await (await db2.execute(
            "SELECT tg_id FROM telegram_users WHERE bot_token = ? AND verified = 1", (bot_token,)
        )).fetchall()
    if not users:
        return {"status": "no_subscribers"}

    bot = Bot(token=bot_token)
    raw = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    exts = data.get("Extensions", [])
    ctype = int(data.get("CallType", 0))
    is_int = ctype == 2 or (is_internal_number(raw) and len(exts)==1 and is_internal_number(exts[0]))

    if et == "start":
        handler = process_internal_start if is_int else process_start
    elif et == "dial":
        handler = process_dial
    elif et == "bridge":
        handler = process_internal_bridge if is_int else process_bridge
    elif et == "hangup":
        handler = process_internal_hangup if is_int else process_hangup
    else:
        return {"status": "ignored"}

    results = []
    for row in users:
        tg = int(row["tg_id"])
        try:
            r = await handler(bot, tg, data)
            results.append({"tg_id": tg, "status": r.get("status")})
        except Exception as e:
            logger.error(f"send to {tg}: {e}")
            results.append({"tg_id": tg, "status": "error"})
    return {"status": "sent", "details": results}

@app.post("/events/{event_type}")
async def receive_event_prefixed(event_type: str, request: Request):
    return await handle_event(event_type, request)

@app.post("/{event_type}")
async def receive_event_root(event_type: str, request: Request):
    return await handle_event(event_type, request)
