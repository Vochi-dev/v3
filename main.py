import logging
import asyncio
from logging.handlers import RotatingFileHandler
import os
import json

from fastapi import FastAPI, Request, Body, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.logger import logger as fastapi_logger
from fastapi.middleware.cors import CORSMiddleware
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
from app.services.postgres import init_pool, close_pool, get_pool

from telegram import Bot
from telegram.error import TelegramError



# ────────────────────────────────────────────────────────────────────────────────
# Импортируем ваши готовые Asterisk-обработчики из папки app/services/calls
# ────────────────────────────────────────────────────────────────────────────────
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup,
    # Новые обработчики для модернизации (17.01.2025)
    process_bridge_create,
    process_bridge_leave,
    process_bridge_destroy,
    process_new_callerid
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

# Добавляем CORS middleware для корректной работы с браузером
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене лучше указать конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/tmpl_static", StaticFiles(directory="app/templates"), name="tmpl_static")


# ────────────────────────────────────────────────────────────────────────────────
# Регистрируем роутеры административной части (CRUD для предприятий и т.п.)
# ────────────────────────────────────────────────────────────────────────────────
from app.routers import admin           # /admin/*
from app.routers.email_users import router as email_users_router   # /admin/email-users
from app.routers.auth_email import router as auth_email_router     # /verify-email/{token}
from app.routers import asterisk
from app.routers.enterprise import router as enterprise_pg_router
from app.routers.mobile import router as mobile_router
from app.routers.sip import router as sip_router
from app.routers.gateway import router as gateway_router

app.include_router(admin.router)
app.include_router(email_users_router)
app.include_router(auth_email_router)
app.include_router(asterisk.router)
app.include_router(enterprise_pg_router, tags=["enterprises_postgresql"])
app.include_router(mobile_router, tags=["mobile"])
app.include_router(sip_router, tags=["sip"])
app.include_router(gateway_router, tags=["gateways"])

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
async def asterisk_start(body: dict = Body(...), request: Request = None):
    """
    При POST /start вызываем process_start из app/services/calls/start.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"START REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_start, body))

@app.post("/dial")
async def asterisk_dial(body: dict = Body(...), request: Request = None):
    """
    При POST /dial вызываем process_dial из app/services/calls/dial.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"DIAL REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_dial, body))

@app.post("/bridge")
async def asterisk_bridge(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge вызываем process_bridge из app/services/calls/bridge.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_bridge, body))

@app.post("/hangup")
async def asterisk_hangup(body: dict = Body(...), request: Request = None):
    """
    При POST /hangup вызываем process_hangup из app/services/calls/hangup.py
    """
    # Добавляем детальное логирование для диагностики
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"HANGUP REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_hangup, body))

# ────────────────────────────────────────────────────────────────────────────────
# Новые эндпоинты для модернизированного AMI-скрипта (17.01.2025)
# ────────────────────────────────────────────────────────────────────────────────

@app.post("/bridge_create")
async def asterisk_bridge_create(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge_create вызываем process_bridge_create из app/services/calls/bridge.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE_CREATE REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_bridge_create, body))

@app.post("/bridge_leave")
async def asterisk_bridge_leave(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge_leave вызываем process_bridge_leave из app/services/calls/bridge.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE_LEAVE REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_bridge_leave, body))

@app.post("/bridge_destroy")
async def asterisk_bridge_destroy(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge_destroy вызываем process_bridge_destroy из app/services/calls/bridge.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE_DESTROY REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_bridge_destroy, body))

@app.post("/new_callerid")
async def asterisk_new_callerid(body: dict = Body(...), request: Request = None):
    """
    При POST /new_callerid вызываем process_new_callerid из app/services/calls/bridge.py
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"NEW_CALLERID REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    return JSONResponse(await _dispatch_to_all(process_new_callerid, body))

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
    pool = await get_pool()
    if not pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    
    async with pool.acquire() as conn:
        # Ищем предприятие по name2 = asterisk_token
        ent_row = await conn.fetchrow(
            "SELECT bot_token FROM enterprises WHERE name2 = $1", 
            asterisk_token
        )
        if not ent_row:
            raise HTTPException(status_code=404, detail="Unknown enterprise token")
        
        bot_token = ent_row["bot_token"]
        
        # Получаем список подписанных пользователей для этого бота
        user_rows = await conn.fetch(
            "SELECT tg_id FROM telegram_users WHERE bot_token = $1",
            bot_token
        )
    
    tg_ids = [int(row["tg_id"]) for row in user_rows]
    if SUPERUSER_TG_ID not in tg_ids:
        tg_ids.append(SUPERUSER_TG_ID)
    return bot_token, tg_ids

async def _dispatch_to_all(handler, body: dict):
    """
    Универсальный диспетчер: получает функцию handler (process_start, process_dial и т. д.),
    вызывает её для каждого chat_id, возвращает результат в формате {"delivered": [...]}
    """
    token = body.get("Token")
    unique_id = body.get("UniqueId", "")
    
    # Детальное логирование для диагностики
    logger.info(f"_dispatch_to_all: Token='{token}', UniqueId='{unique_id}', body keys: {list(body.keys())}")
    
    # Определяем тип события из URL
    event_type = "unknown"
    import inspect
    frame = inspect.currentframe()
    try:
        caller_name = frame.f_back.f_code.co_name
        if "hangup" in caller_name:
            event_type = "hangup"
        elif "bridge_create" in caller_name:
            event_type = "bridge_create"
        elif "bridge_leave" in caller_name:
            event_type = "bridge_leave"
        elif "bridge_destroy" in caller_name:
            event_type = "bridge_destroy"
        elif "new_callerid" in caller_name:
            event_type = "new_callerid"
        elif "bridge" in caller_name:
            event_type = "bridge"
        elif "dial" in caller_name:
            event_type = "dial"
        elif "start" in caller_name:
            event_type = "start"
        logger.info(f"Detected event_type: {event_type}")
    finally:
        del frame
    
    # Сохраняем событие в PostgreSQL
    from app.services.events import save_asterisk_event, mark_telegram_sent
    await save_asterisk_event(event_type, unique_id, token, body)
    
    try:
        bot_token, tg_ids = await _get_bot_and_recipients(token)
        logger.info(f"Found bot_token: {bot_token}, tg_ids: {tg_ids}")
    except Exception as e:
        logger.error(f"Failed to get bot and recipients for token '{token}': {e}")
        return {"delivered": [{"status": "error", "error": f"Failed to get bot: {e}"}]}
    
    bot = Bot(token=bot_token)
    results = []
    
    # 🔗 Для hangup событий генерируем общий UUID токен для всех chat_id
    if event_type == "hangup" and unique_id:
        import uuid
        shared_uuid_token = str(uuid.uuid4())
        body["_shared_uuid_token"] = shared_uuid_token
        logger.info(f"Generated shared UUID token for hangup {unique_id}: {shared_uuid_token}")
    
    telegram_success = False

    for chat_id in tg_ids:
        try:
            result = await handler(bot, chat_id, body)
            if result and result.get("status") == "error":
                # Обработчик вернул ошибку
                results.append({"chat_id": chat_id, "status": "error", "error": result.get("error", "Unknown error")})
                logger.error(f"Handler returned error for chat_id {chat_id}: {result.get('error')}")
            else:
                # Успешный результат
                results.append({"chat_id": chat_id, "status": "ok"})
                telegram_success = True
                logger.info(f"Successfully sent to chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Asterisk dispatch to {chat_id} failed: {e}")
            results.append({"chat_id": chat_id, "status": "error", "error": str(e)})
    
    # Обновляем флаг telegram_sent если хотя бы одно сообщение отправлено успешно
    if telegram_success and unique_id:
        await mark_telegram_sent(unique_id, event_type)
        logger.info(f"Marked telegram_sent for {event_type} event {unique_id}")
    elif telegram_success and not unique_id:
        logger.warning(f"Telegram sent successfully but UniqueId is empty for {event_type}")
    
    return {"delivered": results}


# ────────────────────────────────────────────────────────────────────────────────
# Прокси-эндпоинт для вызова сервиса скачивания записей
# ────────────────────────────────────────────────────────────────────────────────
import httpx

@app.post("/api/recordings/force-download/{enterprise_number}")
async def force_download_recordings(enterprise_number: str):
    """Прокси-эндпоинт для принудительного скачивания записей с сервиса call_download.py"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:8012/recordings/force-download/{enterprise_number}",
                timeout=30.0
            )
            return response.json()
    except httpx.TimeoutException:
        logger.error(f"Timeout calling call_download service for enterprise {enterprise_number}")
        return {
            "success": False, 
            "error": "Timeout - сервис скачивания записей не отвечает"
        }
    except httpx.ConnectError:
        logger.error(f"Connection error calling call_download service for enterprise {enterprise_number}")
        return {
            "success": False, 
            "error": "Сервис скачивания записей недоступен"
        }
    except Exception as e:
        logger.error(f"Error calling call_download service for enterprise {enterprise_number}: {e}")
        return {
            "success": False, 
            "error": f"Ошибка вызова сервиса: {str(e)}"
        }
