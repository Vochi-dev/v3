# main.py
# -*- coding: utf-8 -*-
"""
ASGI-приложение FastAPI для приёма вебхуков, фоновых задач и админ-панели.
Polling Telegram-бота запускается отдельно через python3 -m app.telegram.bot
"""

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import Bot

# ────────── Конфигурация ──────────
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"

app = FastAPI()

# ─── монтируем статику для админки (css, js, иконки) ───
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ─── Jinja2 для рендеринга шаблонов ───
templates = Jinja2Templates(directory="app/templates")

# ─── health-check ───
@app.get("/health")
async def health():
    return {"status": "ok"}

# ─── Telegram-бот для уведомлений о звонках ───
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ─── подключаем роутеры ──────────────────────────
from app.routers import admin, enterprise, user_requests, auth_email

app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(auth_email.router)

# ─── кэши для звонков ───────────────────────────
from app.services.events import init_database_tables, load_hangup_message_history, save_asterisk_event
from app.services.calls import process_start, process_dial, process_bridge, process_hangup, create_resend_loop

dial_cache, bridge_store, active_bridges = {}, {}, {}

# ─── логирование событий ────────────────────────
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ─── фоновые задачи при старте ───────────────────
@app.on_event("startup")
async def startup_tasks():
    # 1) Инициализация БД и загрузка истории hangup
    init_database_tables()
    load_hangup_message_history()

    # 2) Фоновая пересылка «мостов»
    asyncio.create_task(
        create_resend_loop(
            dial_cache, bridge_store, active_bridges,
            notify_bot, TELEGRAM_CHAT_ID
        )
    )

    # 3) А вот polling-бот запускаем отдельно:
    #    python3 -m app.telegram.bot

# ─── вебхук для Asterisk-событий ──────────────────
@app.post("/{event_type}")
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
