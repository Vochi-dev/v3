from fastapi import FastAPI, Request
import logging
import asyncio
from telegram import Bot

# 🔧 Жестко прописанные значения токена и chat_id
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID = "374573193"

print(f"🔑 TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN}")

from app.services.events import (
    init_database_tables,
    load_hangup_message_history,
    save_asterisk_event,
    save_telegram_message,
)
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup,
    create_resend_loop,
)

# ✅ Подключаем только admin роутер
from app.routers import admin, admin_email  # ✅ добавили импорт

app = FastAPI()
bot = Bot(token=TELEGRAM_BOT_TOKEN)

app.include_router(admin.router)
app.include_router(admin_email.router)  # ✅ добавили регистрацию


# ✅ Регистрируем admin роутер
app.include_router(admin.router)

# 🧠 in-memory stores
dial_cache = {}
bridge_store = {}
active_bridges = {}

# 📜 Логирование
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 🚀 Стартовые задачи
@app.on_event("startup")
async def on_startup():
    init_database_tables()
    load_hangup_message_history()
    asyncio.create_task(
        create_resend_loop(
            dial_cache,
            bridge_store,
            active_bridges,
            bot,
            TELEGRAM_CHAT_ID
        )
    )

# 📥 Обработка событий Asterisk
@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")

    save_asterisk_event(et, uid, token, data)

    handlers = {
        "start": process_start,
        "dial": process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    handler = handlers.get(et)
    if handler:
        return await handler(bot, TELEGRAM_CHAT_ID, data)

    return {"status": "ignored"}
