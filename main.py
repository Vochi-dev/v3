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

# ───────── конфиг ─────────
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ───────── health ─────────
@app.get("/health")
async def health():
    logging.debug("GET /health")
    return {"status": "ok"}

# ───────── подключаем роутеры ─────────
from app.routers import admin, enterprise, user_requests, auth_email, email_users  # noqa: E402

app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(email_users.router)
app.include_router(auth_email.router)

# ───────── звонковая логика ─────────
from app.services.events import (  # noqa: E402
    init_database_tables, load_hangup_message_history, save_asterisk_event
)
from app.services.calls import (   # noqa: E402
    process_start, process_dial, process_bridge,
    process_hangup, create_resend_loop
)

dial_cache, bridge_store, active_bridges = {}, {}, {}

@app.on_event("startup")
async def startup_tasks():
    logging.debug("Startup: init DB and history")
    # init DB & history
    init_database_tables()
    load_hangup_message_history()

    # фоновые «мосты»
    asyncio.create_task(
        create_resend_loop(
            dial_cache, bridge_store, active_bridges,
            notify_bot, TELEGRAM_CHAT_ID
        )
    )
    logging.debug("Background bridge loop started")

# ───────── вебхуки Asterisk ─────────
@app.post("/events/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    logging.debug(f"Received Asterisk event: {event_type} — {data}")
    et   = event_type.lower()
    uid  = data.get("UniqueId", "")
    token= data.get("Token", "")

    save_asterisk_event(et, uid, token, data)

    handlers = {
        "start":  process_start,
        "dial":   process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    if handler := handlers.get(et):
        return await handler(notify_bot, TELEGRAM_CHAT_ID, data)
    return {"status": "ignored"}
