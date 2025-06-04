import logging
import asyncio
from logging.handlers import RotatingFileHandler
import os

from fastapi import FastAPI, Request, Body, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.logger import logger as fastapi_logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.database import (
    get_all_enterprises,
    get_enterprise_by_number,
    add_enterprise,
    update_enterprise,
    delete_enterprise,
)
from app.services.enterprise import send_message_to_bot
from app.services.bot_status import check_bot_status
from app.services.db import get_all_bot_tokens
from app.services.postgres import init_pool, close_pool

from telegram import Bot
from telegram.error import TelegramError

import aiosqlite
from app.config import DB_PATH

# ────────────────────────────────────────────────────────────────────────────────
# Импортируем ваши готовые Asterisk-обработчики из папки app/services/calls
# ────────────────────────────────────────────────────────────────────────────────
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup
)

# ────────────────────────────────────────────────────────────────────────────────
# TG-ID «главного» пользователя (чтобы он всегда получал уведомления)
# ────────────────────────────────────────────────────────────────────────────────
SUPERUSER_TG_ID = 374573193

# Создаем директорию для логов, если её нет
os.makedirs("logs", exist_ok=True)

# Настройка основного логгера
main_handler = RotatingFileHandler(
    "logs/app.log",
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding="utf-8"
)
main_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)

# Настройка логгера для FastAPI/Uvicorn
uvicorn_handler = RotatingFileHandler(
    "logs/uvicorn.log",
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding="utf-8"
)
uvicorn_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)

# Настройка логгера для доступа
access_handler = RotatingFileHandler(
    "logs/access.log",
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding="utf-8"
)
access_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)

# Конфигурация логгеров
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[main_handler]
)
logger = logging.getLogger(__name__)

# Настройка логгеров uvicorn
uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.setLevel(logging.DEBUG)
uvicorn_logger.addHandler(uvicorn_handler)

uvicorn_error_logger = logging.getLogger("uvicorn.error")
uvicorn_error_logger.setLevel(logging.DEBUG)
uvicorn_error_logger.addHandler(uvicorn_handler)

uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.setLevel(logging.DEBUG)
uvicorn_access_logger.addHandler(access_handler)

fastapi_logger.setLevel(logging.DEBUG)
fastapi_logger.addHandler(main_handler)

# --- Создаём FastAPI с debug=True для расширенного логирования ---
app = FastAPI(debug=True)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ────────────────────────────────────────────────────────────────────────────────
# Регистрируем роутеры административной части (CRUD для предприятий и т.п.)
# ────────────────────────────────────────────────────────────────────────────────
from app.routers import admin           # /admin/*
from app.routers.email_users import router as email_users_router   # /admin/email-users
from app.routers.auth_email import router as auth_email_router     # /verify-email/{token}
from app.routers import asterisk
from app.routers.enterprise import router as enterprise_pg_router

app.include_router(admin.router)
app.include_router(email_users_router)
app.include_router(auth_email_router)
app.include_router(asterisk.router)
app.include_router(enterprise_pg_router, prefix="/admin", tags=["enterprises_postgresql"])

# --- Обработчик ошибок валидации запросов (422) ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    fastapi_logger.error(
        f"Validation error for {request.method} {request.url}\nErrors: {exc.errors()}"
    )
    try:
        body = await request.body()
        fastapi_logger.debug(f"Request body: {body.decode('utf-8')}")
    except Exception as e:
        fastapi_logger.debug(f"Could not read request body: {e}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = asyncio.get_event_loop().time()
        
        # Логируем детали запроса
        logger.info(
            "Request: %s %s [Client: %s, User-Agent: %s]",
            request.method,
            request.url,
            request.client.host if request.client else "Unknown",
            request.headers.get("user-agent", "Unknown")
        )
        
        try:
            response = await call_next(request)
            
            # Логируем успешный ответ
            process_time = (asyncio.get_event_loop().time() - start_time) * 1000
            logger.info(
                "Response: %d [%0.2fms] %s %s",
                response.status_code,
                process_time,
                request.method,
                request.url
            )
            
            return response
            
        except Exception as e:
            # Логируем ошибки
            logger.exception(
                "Error processing request: %s %s - %s",
                request.method,
                request.url,
                str(e)
            )
            raise

app.add_middleware(LoggingMiddleware)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/admin/enterprises")

@app.post("/start")
async def asterisk_start(body: dict = Body(...)):
    """
    При POST /start вызываем process_start из app/services/calls/start.py
    """
    return JSONResponse(await _dispatch_to_all(process_start, body))

@app.post("/dial")
async def asterisk_dial(body: dict = Body(...)):
    """
    При POST /dial вызываем process_dial из app/services/calls/dial.py
    """
    return JSONResponse(await _dispatch_to_all(process_dial, body))

@app.post("/bridge")
async def asterisk_bridge(body: dict = Body(...)):
    """
    При POST /bridge вызываем process_bridge из app/services/calls/bridge.py
    """
    return JSONResponse(await _dispatch_to_all(process_bridge, body))

@app.post("/hangup")
async def asterisk_hangup(body: dict = Body(...)):
    """
    При POST /hangup вызываем process_hangup из app/services/calls/hangup.py
    """
    return JSONResponse(await _dispatch_to_all(process_hangup, body))

# ────────────────────────────────────────────────────────────────────────────────
# Раздел, связанный с запуском Aiogram-ботов, временно отключён,
# чтобы не было ошибки NameError для setup_dispatcher.
# Если вы хотите вернуть этот функционал, убедитесь, что
# у вас есть функция setup_dispatcher и соответствующие импорты.
# ────────────────────────────────────────────────────────────────────────────────

# async def start_bot(enterprise_number: str, token: str):
#     bot = AiogramBot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
#     dp = await setup_dispatcher(bot, enterprise_number)
#     try:
#         logger.info(f"Starting bot for enterprise {enterprise_number}")
#         await dp.start_polling(bot)
#     finally:
#         await bot.session.close()

# async def start_all_bots():
#     tokens = await get_all_bot_tokens()
#     tasks = []
#     for enterprise_number, token in tokens.items():
#         if token and token.strip():
#             tasks.append(asyncio.create_task(start_bot(enterprise_number, token)))
#     await asyncio.gather(*tasks)

# @app.on_event("startup")
# async def on_startup():
#     logger.info("Starting all telegram bots…")
#     asyncio.create_task(start_all_bots())

# @app.on_event("shutdown")
# async def shutdown_event():
#     logger.info("Shutting down bots gracefully…")
#     for task in asyncio.all_tasks():
#         task.cancel()

@app.on_event("startup")
async def startup():
    """Инициализация при запуске приложения"""
    await init_pool()

@app.on_event("shutdown")
async def shutdown():
    """Очистка при остановке приложения"""
    await close_pool()

async def _get_bot_and_recipients(asterisk_token: str) -> tuple[str, list[int]]:
    """
    Возвращает bot_token и список целевых chat_id по asterisk_token.
    Гарантирует, что SUPERUSER_TG_ID там есть всегда.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM enterprises WHERE name2 = ?",
            (asterisk_token,)
        )
        ent = await cur.fetchone()
        if not ent:
            raise HTTPException(status_code=404, detail="Unknown enterprise token")
        bot_token = ent["bot_token"]

        cur = await db.execute(
            "SELECT tg_id FROM telegram_users WHERE bot_token = ? AND verified = 1",
            (bot_token,)
        )
        rows = await cur.fetchall()

    tg_ids = [int(r["tg_id"]) for r in rows]
    if SUPERUSER_TG_ID not in tg_ids:
        tg_ids.append(SUPERUSER_TG_ID)
    return bot_token, tg_ids

async def _dispatch_to_all(handler, body: dict):
    """
    Универсальный диспетчер: получает функцию handler (process_start, process_dial и т. д.),
    вызывает её для каждого chat_id, возвращает результат в формате {"delivered": [...]}
    """
    token = body.get("Token")
    bot_token, tg_ids = await _get_bot_and_recipients(token)
    bot = Bot(token=bot_token)
    results = []

    for chat_id in tg_ids:
        try:
            await handler(bot, chat_id, body)
            results.append({"chat_id": chat_id, "status": "ok"})
        except Exception as e:
            logger.error(f"Asterisk dispatch to {chat_id} failed: {e}")
            results.append({"chat_id": chat_id, "status": "error", "error": str(e)})
    return {"delivered": results}
