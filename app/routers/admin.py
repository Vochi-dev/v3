# /root/asterisk-webhook/main.py
from fastapi import FastAPI, Request
import asyncio, logging
from telegram import Bot                             # python-telegram-bot (уведомления от Asterisk)

# ───────── базовые константы (при желании вынесите в .env / app.config) ─────────
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"
print(f"🔑 TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN}")

# ───────── сервис-слой Asterisk ─────────
from app.services.events import (
    init_database_tables, load_hangup_message_history, save_asterisk_event,
)
from app.services.calls import (
    process_start, process_dial, process_bridge, process_hangup, create_resend_loop,
)

# ───────── роутеры FastAPI ─────────
from app.routers import (
    admin,              # /admin/…            — логин, дашборд, email-лист
    enterprise,         # /admin/enterprises/…
    user_requests,      # /admin/requests/…
    auth_email          # /verify-email/{token}
)

# ───────── FastAPI-приложение + бот для уведомлений ─────────
app           = FastAPI()
tg_notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Подключаем роутеры **без дополнительных prefix-ов**
app.include_router(admin.router)
app.include_router(enterprise.router)
app.include_router(user_requests.router)
app.include_router(auth_email.router)

# in-memory кэши звонков
dial_cache, bridge_store, active_bridges = {}, {}, {}

# ───────── логирование ─────────
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# ───────── lifecycle ─────────
@app.on_event("startup")
async def startup_tasks():
    # 1) База
    init_database_tables()
    load_hangup_message_history()

    # 2) фон для «мостов»
    asyncio.create_task(
        create_resend_loop(
            dial_cache, bridge_store, active_bridges,
            tg_notify_bot, TELEGRAM_CHAT_ID,
        )
    )

    # 3) aiogram-бот (long-polling)
    from app.telegram.bot import dp          # импорт здесь — избегаем циклов
    asyncio.create_task(dp.start_polling())

# ───────── приём веб-хуков Asterisk ─────────
@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data  = await request.json()
    et    = event_type.lower()
    uid   = data.get("UniqueId", "")
    token = data.get("Token", "")

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
