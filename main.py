# main.py (в корне проекта)
from fastapi import FastAPI, Request
import asyncio, logging
from telegram import Bot

# ──────────── базовые константы ────────────
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"

# ──────────── импорты сервисов и роутеров ────────────
from app.services.events import (
    init_database_tables, load_hangup_message_history,
    save_asterisk_event,
)
from app.services.calls import (
    process_start, process_dial, process_bridge,
    process_hangup, create_resend_loop,
)
from app.routers import (
    admin, enterprise, user_requests, auth_email
)

# ──────────── приложение FastAPI и бот для нотификаций ────────────
app = FastAPI()
tg_notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(auth_email.router)

dial_cache, bridge_store, active_bridges = {}, {}, {}

logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

@app.on_event("startup")
async def startup_tasks():
    # 1. инициализация БД и истории
    init_database_tables()
    load_hangup_message_history()

    # 2. фоновый цикл пересылки «мостов»
    asyncio.create_task(
        create_resend_loop(
            dial_cache, bridge_store, active_bridges,
            tg_notify_bot, TELEGRAM_CHAT_ID
        )
    )

    # 3. запуск aiogram-бота
    from app.telegram.bot import bot, dp, setup_bot
    await setup_bot()
    asyncio.create_task(dp.start_polling(bot))

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data   = await request.json()
    et     = event_type.lower()
    uid    = data.get("UniqueId", "")
    token  = data.get("Token", "")

    save_asterisk_event(et, uid, token, data)

    handlers = {
        "start":  process_start,
        "dial":   process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    if (handler := handlers.get(et)):
        return await handler(tg_notify_bot, TELEGRAM_CHAT_ID, data)

    return {"status": "ignored"}
