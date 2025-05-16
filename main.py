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

import sys
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from telegram import Bot, TelegramError

from app.config import TELEGRAM_BOT_TOKEN, DB_PATH
import aiosqlite

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

# ───────── настройка логирования ─────────
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logger.addHandler(console_handler)
file_handler = logging.FileHandler("asterisk_events.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(file_handler)

# ───────── FastAPI & шаблоны ─────────
app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

# ───────── middleware для логирования всех запросов ─────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = await request.body()
    logger.debug(
        f"Incoming request: {request.method} {request.url} — Body: "
        f"{body.decode('utf-8', errors='ignore')}"
    )
    return await call_next(request)

# ───────── бот для фоновых уведомлений (startup) ─────────
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)


async def broadcast_to_verified_subscribers(text: str):
    """
    Рассылает text всем telegram_users, у которых verified=1.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tg_id, bot_token FROM telegram_users WHERE verified = 1"
        )
        users = await cur.fetchall()
    for row in users:
        bot = Bot(token=row["bot_token"])
        try:
            await bot.send_message(chat_id=int(row["tg_id"]), text=text)
            logger.debug(f"Broadcast to {row['tg_id']}: {text!r}")
        except TelegramError as e:
            logger.error(f"Failed to broadcast to {row['tg_id']}: {e}")


@app.on_event("startup")
async def startup_tasks():
    logger.debug("Startup: init DB tables and load hangup history")
    await init_database_tables()
    await load_hangup_message_history()

    # 1) уведомляем всех проверённых подписчиков о старте
    logger.debug("Startup: broadcasting service start")
    await broadcast_to_verified_subscribers("✅ Сервис Asterisk-webhook запущен и готов к приёму событий.")

    # 2) запускаем цикл пересылки
    logger.debug("Starting background resend loop")
    asyncio.create_task(
        create_resend_loop(notify_bot, DB_PATH)
    )


@app.get("/health")
async def health():
    logger.debug("GET /health")
    return {"status": "ok"}


async def handle_event(event_type: str, request: Request):
    data = await request.json()
    logger.debug(f"Received Asterisk event: {event_type} — {data}")

    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")

    # сохраняем само событие
    await save_asterisk_event(et, uid, token, data)

    # ищем предприятие по токену
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM enterprises WHERE name2 = ?", (token,)
        )
        ent = await cur.fetchone()

    if not ent:
        logger.warning("No enterprise found for token %r", token)
        return {"status": "no_such_bot"}

    bot_token = ent["bot_token"]

    # собираем проверённых подписчиков этого бота
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        cur2 = await db2.execute(
            "SELECT tg_id FROM telegram_users WHERE bot_token = ? AND verified = 1",
            (bot_token,),
        )
        users = await cur2.fetchall()

    if not users:
        logger.warning("No verified telegram_users for bot_token %r", bot_token)
        return {"status": "no_subscribers"}

    bot = Bot(token=bot_token)

    # определяем, внутренний ли это звонок
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    exts = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int = (
        call_type == 2 or
        (is_internal_number(raw_phone) and
         len(exts) == 1 and
         is_internal_number(exts[0]))
    )

    # выбираем нужный обработчик
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
        tg_id = int(row["tg_id"])
        try:
            res = await handler(bot, tg_id, data)
            results.append({"tg_id": tg_id, "status": res.get("status")})
        except Exception as e:
            logger.error(f"Error sending to {tg_id}: {e}")
            results.append({"tg_id": tg_id, "status": "error", "error": str(e)})

    return {"status": "sent", "details": results}


# два POST-маршрута: с префиксом и без
@app.post("/events/{event_type}")
async def receive_event_prefixed(event_type: str, request: Request):
    return await handle_event(event_type, request)


@app.post("/{event_type}")
async def receive_event_root(event_type: str, request: Request):
    return await handle_event(event_type, request)
