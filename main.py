# main.py
# -*- coding: utf-8 -*-
"""
FastAPI-приложение:
— /health
— /admin/...        админ-панель
— /events/...       вебхуки Asterisk (отдельно от /admin)
Polling Telegram-бота запускается отдельно: python3 -m app.telegram.bot
"""

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from telegram import Bot

# ───────── конфиг ─────────
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ───────── health ─────────
@app.get("/health")
async def health():
    return {"status": "ok"}

# ───────── подключаем роутеры ─────────
from app.routers import admin, enterprise, user_requests, auth_email, email_users

app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(email_users.router)
app.include_router(auth_email.router)

# ───────── звонковая логика ─────────
from app.services.events import (
    init_database_tables, load_hangup_message_history, save_asterisk_event
)
from app.services.calls import (
    process_start, process_dial, process_bridge,
    process_hangup, create_resend_loop
)

dial_cache, bridge_store, active_bridges = {}, {}, {}

logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

@app.on_event("startup")
async def startup_tasks():
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
    # polling Telegram-бота — убрано, запускаем отдельно

# ───────── вебхуки Asterisk ─────────
@app.post("/events/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
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
