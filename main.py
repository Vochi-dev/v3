# main.py
# -*- coding: utf-8 -*-
"""
FastAPI-приложение:
— /health
— /admin/...        админ-панель
— /events/...       вебхуки Asterisk (отдельно от /admin)
Polling Telegram-бота запускается отдельно: python3 -m app.telegram.bot
"""

import sys
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from telegram import Bot

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DB_PATH
import aiosqlite

from app.services.events import (
    init_database_tables,
    load_hangup_message_history,
)
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup,
    create_resend_loop,
)

# ───────── настройка логирования ─────────
logger = logging.getLogger()  # корневой логгер
logger.setLevel(logging.DEBUG)

# консольный хэндлер
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
console_handler.setFormatter(console_fmt)
logger.addHandler(console_handler)

# файловый хэндлер для событий Asterisk
file_handler = logging.FileHandler("asterisk_events.log")
file_handler.setLevel(logging.INFO)
file_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(file_fmt)
logger.addHandler(file_handler)

# ───────── FastAPI & шаблоны ─────────
app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

# создаём бота для внутренних уведомлений и лупа
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ───────── routers ─────────
from app.routers import admin, enterprise, user_requests, auth_email, email_users  # noqa: E402

app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(email_users.router)
app.include_router(auth_email.router)

# ───────── состояние мостов ─────────
dial_cache = {}
bridge_store = {}
active_bridges = {}

@app.on_event("startup")
async def startup_tasks():
    logger.debug("Startup: init DB tables and load hangup history")
    # создаём таблицы, если нужно
    await init_database_tables()
    # загружаем историю hangup в память (для reply_to)
    hangup_map = await load_hangup_message_history()
    # при необходимости сохраняем hangup_map в глобал (внутри events или calls)
    # запустим фоновые задачи
    logger.debug("Starting background resend loop")
    asyncio.create_task(
        create_resend_loop(
            dial_cache,
            bridge_store,
            active_bridges,
            notify_bot,
            TELEGRAM_CHAT_ID,
        )
    )

@app.get("/health")
async def health():
    logger.debug("GET /health")
    return {"status": "ok"}

@app.post("/events/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    logger.debug(f"Received Asterisk event: {event_type} — {data}")

    et    = event_type.lower()
    uid   = data.get("UniqueId", "")
    token = data.get("Token", "")

    # сохраняем в БД (всегда)
    from app.services.events import save_asterisk_event
    await save_asterisk_event(et, uid, token, data)

    # ищем предприятие по Asterisk-токену (колонка name2)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token, chat_id FROM enterprises WHERE name2 = ?",
            (token,),
        )
        ent = await cur.fetchone()

    if not ent:
        logger.warning("No enterprise found for token %r", token)
        return {"status": "no_such_bot"}

    bot_token = ent["bot_token"]
    chat_id   = ent["chat_id"]
    bot       = Bot(token=bot_token)

    # выбор обработчика
    handlers = {
        "start":  process_start,
        "dial":   process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    if handler := handlers.get(et):
        return await handler(bot, chat_id, data)

    return {"status": "ignored"}
