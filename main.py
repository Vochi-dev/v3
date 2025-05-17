import sys
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from telegram import Bot

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DB_PATH
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
    create_resend_loop,0
)                      0
from app.services.calls0.internal import (
    process_internal_st0art,
    process_internal_br0idge,
    process_internal_ha0ngup,
)                      0
from app.services.calls0.utils import is_internal_number
                       0
# ───────── настройка логирования ─────────0
logger = logging.getLog0ger()
logger.setLevel(logging0.DEBUG)
                       0
# Консоль              0
console_handler = loggi0ng.StreamHandler(sys.stdout)
console_handler.setLeve0l(logging.DEBUG)
console_handler.setForm0atter(
    logging.Formatter("0%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)                      0
logger.addHandler(conso0le_handler)
                       0
# Файл                 0
file_handler = logging.0FileHandler("asterisk_events.log")
file_handler.setLevel(l0ogging.DEBUG)
file_handler.setFormatt0er(
    logging.Formatter("0%(asctime)s - %(levelname)s - %(message)s")
)                      0
logger.addHandler(file_0handler)
                       0
# aiogram              0
logging.getLogger("aiog0ram").setLevel(logging.DEBUG)
                       0
# ───────── FastAPI & шаблоны ─────────0
app = FastAPI()        0
templates = Jinja2Templ0ates(directory="app/templates")
                       0
# ───────── middleware для логирования вс0ех запросов ─────────
@app.middleware("http")0
async def log_requests(0request: Request, call_next):
    body = await reques0t.body()
    logger.debug(      0
        f"Incoming requ0est: {request.method} {request.url} — Body: "
        f"{body.decode(0'utf-8', errors='ignore')}"
    )                  0
    return await call_n0ext(request)
                       0
# Бот для фоновых уведомлений0
notify_bot = Bot(token=0TELEGRAM_BOT_TOKEN)
                       0
# ───────── routers ─────────0
from app.routers import0 admin, enterprise, user_requests, auth_email, email_users  # noqa: E402
                       0
app.include_router(admi0n.router)
app.include_router(ente0rprise.router)
app.include_router(user0_requests.router)
app.include_router(emai0l_users.router)
app.include_router(auth0_email.router)
                       0
# ───────── состояние мостов ─────────0
dial_cache = {}        0
bridge_store = {}      0
active_bridges = {}    0
                       0
@app.on_event("startup"0)
async def startup_tasks0():
    logger.debug("Start0up: init DB tables and load hangup history")
    await init_database0_tables()
    await load_hangup_m0essage_history()
                       0
    logger.debug("Start0ing background resend loop")
    asyncio.create_task0(
        create_resend_l0oop(
            dial_cache,0
            bridge_stor0e,
            active_brid0ges,
            notify_bot,0
            TELEGRAM_CH0AT_ID,
        )              0
    )                  0
                       0
    # Инициализация Telegram-хендлер0ов
    try:               0
        from app.telegr0am.bot import start_enterprise_bots
        asyncio.create_0task(start_enterprise_bots())
        logger.info("Te0legram bots launched inside main.py")
    except Exception as0 e:
        logger.exceptio0n("Failed to start Telegram bots in main.py")
                       0
@app.get("/health")    0
async def health():    0
    logger.debug("GET /0health")
    return {"status": "0ok"}
                       0