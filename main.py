# /root/asterisk-webhook/main.py
from fastapi import FastAPI, Request
import logging, asyncio
from telegram import Bot                          # python-telegram-bot (для уведомлений Asterisk)

# ────────────────────────────
# Константы (позже можно вынести в .env / app.config)
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"
print(f"🔑 TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN}")
# ────────────────────────────

# Сервисы Asterisk-интеграции
from app.services.events import (
    init_database_tables, load_hangup_message_history,
    save_asterisk_event,
)
from app.services.calls import (
    process_start, process_dial, process_bridge,
    process_hangup, create_resend_loop,
)

# Роутеры FastAPI
from app.routers import admin, auth_email          # auth_email → /verify-email/{token}

# ────────────────────────────
app = FastAPI()

# бот для уведомлений (старый python-telegram-bot)
tg_notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# регистрируем роутеры админ-панели и подтверждения email
app.include_router(admin.router, prefix="/admin")
app.include_router(auth_email.router)              # без префикса

# in-memory кэши звонков
dial_cache: dict  = {}
bridge_store: dict = {}
active_bridges: dict = {}

# логирование
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ─────────────── FastAPI startup ───────────────
@app.on_event("startup")
async def startup_tasks() -> None:
    # 1. подготовка БД
    init_database_tables()
    load_hangup_message_history()

    # 2. фоновая переотправка «мостов»
    asyncio.create_task(
        create_resend_loop(
            dial_cache, bridge_store, active_bridges,
            tg_notify_bot, TELEGRAM_CHAT_ID
        )
    )

    # 3. запускаем aiogram-бота (опрос long-polling)
    #    Импорт внутри функции, чтобы избежать циклических зависимостей
    from app.telegram.bot import dp                # aiogram Dispatcher
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling())           # aiogram Bot создаётся в app.telegram.bot

# ─────────────── Asterisk веб-хуки ───────────────
@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    """
    Принимаем события Asterisk (/start /dial /bridge /hangup)
    """
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
        return await handler(tg_notify_bot, TELEGRAM_CHAT_ID, data)

    return {"status": "ignored"}
