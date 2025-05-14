# main.py (корень проекта)
# -*- coding: utf-8 -*-
"""
ASGI приложение FastAPI для приёма вебхуков и запуска фоновых задач,
но без автозапуска Telegram-бота (polling).
Для запуска бота отдельно используйте:
    python3 -m app.telegram.bot
"""

import asyncio
import logging

from fastapi import FastAPI, Request
from telegram import Bot

# ────────────── Конфигурация ──────────────────────────────────────
# Telegram Bot для уведомлений о звонках (по старому сценарию)
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"

# ─────────── Импорты сервисов и роутеров Asterisk ────────────────
from app.services.events import (
    init_database_tables,
    load_hangup_message_history,
    save_asterisk_event,
)
from app.services.calls import (
    process_start, process_dial, process_bridge,
    process_hangup, create_resend_loop,
)
from app.routers import (
    admin, enterprise, user_requests, auth_email
)

# ─────────── FastAPI-приложение и бот для нотификаций ───────────
app = FastAPI()
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(auth_email.router)

# Кэши и хранилища для звонков
dial_cache, bridge_store, active_bridges = {}, {}, {}

# Логирование
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ─────────── Lifecycle: запуск фоновых задач ─────────────────────
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

    # ** УБРАН автозапуск Telegram-бота (polling) **
    # Если нужен polling, запускайте его отдельно:
    # python3 -m app.telegram.bot

# ─────────── Эндпоинт приёма вебхуков Asterisk ─────────────────
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
