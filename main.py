# /root/asterisk-webhook/main.py
from fastapi import FastAPI, Request
import logging, asyncio
from telegram import Bot

# --- базовые константы (пока жёстко, можно вынести в .env)
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"
print(f"🔑 TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN}")

# --- служебные сервисы
from app.services.events import (
    init_database_tables, load_hangup_message_history,
    save_asterisk_event, save_telegram_message,
)
from app.services.calls import (
    process_start, process_dial, process_bridge,
    process_hangup, create_resend_loop,
)

# --- роутеры
from app.routers import admin, auth_email   # ← добавили auth_email

# -----------------------------------------------------------------------------
app = FastAPI()
bot = Bot(token=TELEGRAM_BOT_TOKEN)

app.include_router(admin.router)        # /admin и всё внутри
app.include_router(auth_email.router)   # /verify-email

# -----------------------------------------------------------------------------
dial_cache, bridge_store, active_bridges = {}, {}, {}

logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -----------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    init_database_tables()
    load_hangup_message_history()
    asyncio.create_task(
        create_resend_loop(
            dial_cache, bridge_store, active_bridges,
            bot, TELEGRAM_CHAT_ID
        )
    )

# -----------------------------------------------------------------------------
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
    handler = handlers.get(et)
    if handler:
        return await handler(bot, TELEGRAM_CHAT_ID, data)

    return {"status": "ignored"}
