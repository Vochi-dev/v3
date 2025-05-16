from dotenv import load_dotenv
load_dotenv()

import os, sys, asyncio, logging
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from telegram import Bot
import aiosqlite

from app.config import DB_PATH
from app.services.events import init_database_tables, load_hangup_message_history
from app.services.calls import create_resend_loop

# импорт роутеров
from app.routers.admin import router as admin_router
from app.routers.health import router as health_router
from app.routers.webhooks import router as webhooks_router

# настройка логирования (как было)
logger = logging.getLogger()
# …

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

# подключаем роутеры
app.include_router(health_router)
app.include_router(admin_router)       # префикс внутри admin.py
app.include_router(webhooks_router)    # webhooks без префикса

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # …

@app.on_event("startup")
async def startup_tasks():
    await init_database_tables()
    await load_hangup_message_history()
    # уведомления и create_resend_loop …

# здесь больше не остаётся ни одного route-handler’а
