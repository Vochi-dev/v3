import logging
import asyncio
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
import os
import json
from functools import wraps
from typing import Optional, Dict, Callable

import asyncpg
from fastapi import FastAPI, Request, Body, HTTPException, status, Form, Cookie
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

# Call Tracer для логирования событий в файлы
from app.utils.call_tracer import (
    log_telegram_event,
    log_asterisk_event
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

# Настройка логгера для тестового предприятия 0367 (Token: 375293332255)
test_enterprise_handler = RotatingFileHandler(
    "logs/0367.log",
    maxBytes=5*1024*1024,  # 5MB
    backupCount=3,
    encoding="utf-8"
)
test_enterprise_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)

# Конфигурация логгеров
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[main_handler]
)
logger = logging.getLogger(__name__)

# Создаем отдельный логгер для тестового предприятия
test_logger = logging.getLogger("test_enterprise_0367")
test_logger.addHandler(test_enterprise_handler)
test_logger.setLevel(logging.DEBUG)
test_logger.propagate = False  # Не передавать в родительский логгер

# ────────────────────────────────────────────────────────────────────────────────
# Call Tracer - универсальное логирование событий для всех юнитов
# Папка: call_tracer/{enterprise_number}/events.log (ротация 14 дней)
# Функции импортированы из app.utils.call_tracer:
#   - get_tracer_logger (alias для get_call_tracer_logger)
#   - log_telegram_event
#   - log_asterisk_event
# ────────────────────────────────────────────────────────────────────────────────
os.makedirs("call_tracer", exist_ok=True)


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

# ══════════════════════════════════════════════════════════════════════════════
# АВТОРИЗАЦИЯ И MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════════

# Конфигурация БД для авторизации
AUTH_DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres", 
    "password": "r/Yskqh/ZbZuvjb2b3ahfg==",
    "database": "postgres"
}

# Маршруты, которые не требуют авторизации
PUBLIC_ROUTES = {
    "/", "/admin", "/admin/login", "/admin/dashboard", "/admin/enterprises",
    "/health", "/docs", "/redoc", "/openapi.json",
    "/start", "/dial", "/bridge", "/hangup",  # Asterisk webhooks
    "/bridge_create", "/bridge_leave", "/bridge_destroy", "/new_callerid",
    "/uon/webhook",
}

async def get_user_from_session_token(session_token: str) -> Optional[Dict]:
    """Получить пользователя по токену сессии"""
    if not session_token:
        return None
    
    try:
        conn = await asyncpg.connect(**AUTH_DB_CONFIG)
        session = await conn.fetchrow(
            """SELECT s.user_id, s.enterprise_number, s.expires_at,
                      u.email, u.first_name, u.last_name, u.is_admin, 
                      u.is_employee, u.is_marketer, u.is_spec1, u.is_spec2
               FROM user_sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.session_token = $1 AND s.expires_at > NOW()""",
            session_token
        )
        await conn.close()
        
        if not session:
            return None
        
        return {
            "user_id": session["user_id"],
            "enterprise_number": session["enterprise_number"],
            "email": session["email"],
            "first_name": session["first_name"],
            "last_name": session["last_name"],
            "is_admin": session["is_admin"],
            "is_employee": session["is_employee"],
            "is_marketer": session["is_marketer"],
            "is_spec1": session["is_spec1"],
            "is_spec2": session["is_spec2"]
        }
    except Exception as e:
        logger.error(f"Ошибка получения пользователя из сессии: {e}")
        return None

class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware для проверки авторизации пользователей"""
    
    async def dispatch(self, request: Request, call_next):
        # Проверяем, требует ли маршрут авторизации
        path = str(request.url.path)
        
        # Публичные маршруты не требуют авторизации
        if any(path.startswith(route) for route in PUBLIC_ROUTES):
            return await call_next(request)
        
        # Специальная обработка для RetailCRM админки
        if path.startswith("/retailcrm-admin/"):
            return await self.handle_retailcrm_admin_auth(request, call_next)
        
        # Специальная обработка для U-ON админки
        if path.startswith("/uon-admin/"):
            return await self.handle_uon_admin_auth(request, call_next)
            
        # Специальная обработка для МойСклад админки
        if path.startswith("/ms-admin/"):
            return await self.handle_ms_admin_auth(request, call_next)
        
        # Для остальных маршрутов проверяем авторизацию
        session_token = request.cookies.get("session_token")
        user = await get_user_from_session_token(session_token)
        
        if not user:
            # Пользователь не авторизован - перенаправляем на главную
            return RedirectResponse(url="/", status_code=302)
        
        # Добавляем пользователя в state запроса
        request.state.user = user
        
        return await call_next(request)
    
    async def handle_retailcrm_admin_auth(self, request: Request, call_next):
        """Обработка авторизации для RetailCRM админки через JWT токены."""
        # Получаем токен из параметров запроса
        token = request.query_params.get("token")
        enterprise_number = request.query_params.get("enterprise_number")
        
        if token and enterprise_number:
            # Проверяем токен через RetailCRM сервис
            try:
                import jwt
                JWT_SECRET_KEY = "vochi-retailcrm-secret-key-2025"  # Совпадает с retailcrm.py
                JWT_ALGORITHM = "HS256"
                
                payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
                if (payload.get("source") == "retailcrm" and 
                    payload.get("enterprise_number") == enterprise_number):
                    
                    # Создаём временного пользователя-админа для RetailCRM админки
                    temp_user = {
                        "id": f"retailcrm_admin_{enterprise_number}",
                        "enterprise_number": enterprise_number,
                        "source": "retailcrm_token",
                        "is_retailcrm_admin": True
                    }
                    request.state.user = temp_user
                    return await call_next(request)
            except Exception:
                pass  # Токен неверный, продолжаем стандартную авторизацию
        
        # Фолбэк: стандартная авторизация через session_token
        session_token = request.cookies.get("session_token")
        user = await get_user_from_session_token(session_token)
        
        if not user:
            # Пользователь не авторизован - перенаправляем на главную
            return RedirectResponse(url="/", status_code=302)
        
        # Добавляем пользователя в state запроса
        request.state.user = user
        return await call_next(request)
    
    async def handle_uon_admin_auth(self, request: Request, call_next):
        """Обработка авторизации для U-ON админки - требует стандартной авторизации предприятия."""
        # Получаем enterprise_number из параметров запроса
        enterprise_number = request.query_params.get("enterprise_number")
        
        # Стандартная авторизация через session_token
        session_token = request.cookies.get("session_token")
        user = await get_user_from_session_token(session_token)
        
        if not user:
            # Пользователь не авторизован - перенаправляем на главную
            return RedirectResponse(url="/", status_code=302)
        
        # Проверяем, что пользователь имеет доступ к указанному предприятию
        if enterprise_number and user.get("enterprise_number") != enterprise_number:
            # Доступ к чужому предприятию запрещен
            return RedirectResponse(url="/", status_code=302)
        
        # Добавляем пользователя в state запроса
        request.state.user = user
        return await call_next(request)
    
    async def handle_ms_admin_auth(self, request: Request, call_next):
        """Обработка авторизации для МойСклад админки - требует стандартной авторизации предприятия."""
        # Получаем enterprise_number из параметров запроса
        enterprise_number = request.query_params.get("enterprise_number")
        
        # Стандартная авторизация через session_token
        session_token = request.cookies.get("session_token")
        user = await get_user_from_session_token(session_token)
        
        if not user:
            # Пользователь не авторизован - перенаправляем на главную
            return RedirectResponse(url="/", status_code=302)
        
        # Проверяем, что пользователь имеет доступ к указанному предприятию
        if enterprise_number and user.get("enterprise_number") != enterprise_number:
            # Доступ к чужому предприятию запрещен
            return RedirectResponse(url="/", status_code=302)
        
        # Добавляем пользователя в state запроса
        request.state.user = user
        return await call_next(request)

def require_auth(func: Callable) -> Callable:
    """Декоратор для обязательной авторизации в endpoint'ах"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Ищем объект Request в аргументах
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        
        if not request:
            raise HTTPException(status_code=500, detail="Request object not found")
        
        # Проверяем наличие пользователя в state
        user = getattr(request.state, 'user', None)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        return await func(*args, **kwargs)
    return wrapper

app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthMiddleware)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Главная страница - перенаправление на авторизацию пользователей"""
    return RedirectResponse(url="/user-auth/", status_code=302)

@app.post("/start")
async def asterisk_start(body: dict = Body(...), request: Request = None):
    """
    При POST /start вызываем process_start из app/services/calls/start.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"START REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_start, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

@app.post("/dial")
async def asterisk_dial(body: dict = Body(...), request: Request = None):
    """
    При POST /dial вызываем process_dial из app/services/calls/dial.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"DIAL REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_dial, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

@app.post("/bridge")
async def asterisk_bridge(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge вызываем process_bridge из app/services/calls/bridge.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_bridge, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

@app.post("/hangup")
async def asterisk_hangup(body: dict = Body(...), request: Request = None):
    """
    При POST /hangup вызываем process_hangup из app/services/calls/hangup.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"HANGUP REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_hangup, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

# ────────────────────────────────────────────────────────────────────────────────
# Новые эндпоинты для модернизированного AMI-скрипта (17.01.2025)
# ────────────────────────────────────────────────────────────────────────────────

@app.post("/bridge_create")
async def asterisk_bridge_create(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge_create вызываем process_bridge_create из app/services/calls/bridge.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE_CREATE REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_bridge_create, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

@app.post("/bridge_leave")
async def asterisk_bridge_leave(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge_leave вызываем process_bridge_leave из app/services/calls/bridge.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE_LEAVE REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_bridge_leave, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

@app.post("/bridge_destroy")
async def asterisk_bridge_destroy(body: dict = Body(...), request: Request = None):
    """
    При POST /bridge_destroy вызываем process_bridge_destroy из app/services/calls/bridge.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"BRIDGE_DESTROY REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_bridge_destroy, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

@app.post("/new_callerid")
async def asterisk_new_callerid(body: dict = Body(...), request: Request = None):
    """
    При POST /new_callerid вызываем process_new_callerid из app/services/calls/bridge.py
    АСИНХРОННО - сразу возвращаем 200 OK, обработка в фоне
    """
    client_ip = request.client.host if request and request.client else "Unknown"
    logger.info(f"NEW_CALLERID REQUEST from {client_ip}: {json.dumps(body, ensure_ascii=False)}")
    
    # Запускаем обработку в фоне, не блокируем ответ
    asyncio.create_task(_dispatch_to_all(process_new_callerid, body))
    
    return JSONResponse({"status": "ok", "message": "Event queued for processing"})

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

async def _get_enterprise_number_by_token(asterisk_token: str) -> Optional[str]:
    """Возвращает enterprise_number по токену (name2/secret/number)."""
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT number FROM enterprises
            WHERE name2 = $1 OR secret = $1 OR number = $1
            LIMIT 1
            """,
            asterisk_token,
        )
        return row["number"] if row else None

async def _apply_incoming_transform_if_any(body: dict) -> None:
    """Нормализует внешний номер по правилу incoming_transform для линии.
    Модифицирует body на месте (Phone/CallerIDNum/ConnectedLineNum)."""
    try:
        token = body.get("Token") or body.get("token")
        trunk = str(body.get("TrunkId") or body.get("Trunk") or body.get("INCALL") or body.get("Incall") or "").strip()
        if not (token and trunk):
            return
        enterprise_number = await _get_enterprise_number_by_token(token)
        if not enterprise_number:
            return
        import httpx
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://127.0.0.1:8020/incoming-transform/{enterprise_number}")
            if r.status_code != 200:
                return
            m = (r.json() or {}).get("map") or {}
            rule = m.get(f"sip:{trunk}") or m.get(f"gsm:{trunk}")
            if not (isinstance(rule, str) and "{" in rule and "}" in rule):
                return
            pref = rule.split("{")[0]
            try:
                n = int(rule.split("{")[1].split("}")[0])
            except Exception:
                return
            # Берём внешний номер из полей события
            candidate = str(body.get("Phone") or body.get("CallerIDNum") or body.get("ConnectedLineNum") or "")
            digits = ''.join(ch for ch in candidate if ch.isdigit())
            if not (n and len(digits) >= n):
                return
            normalized = f"{pref}{digits[-n:]}"
            # Обновляем основные поля, чтобы все обработчики видели нормализованный номер
            body["Phone"] = normalized
            # Если CallerIDNum выглядел как внешний, тоже обновим
            if body.get("CallerIDNum") and not str(body.get("CallerIDNum")).isdigit():
                body["CallerIDNum"] = normalized
            # ConnectedLineNum оставляем как есть (это чаще внутренний)
    except Exception as e:
        logger.warning(f"incoming_transform normalize failed: {e}")

async def _dispatch_to_all(handler, body: dict):
    """
    Универсальный диспетчер: получает функцию handler (process_start, process_dial и т. д.),
    вызывает её для каждого chat_id, возвращает результат в формате {"delivered": [...]}
    """
    token = body.get("Token")
    unique_id = body.get("UniqueId", "")
    
    # Детальное логирование для диагностики
    logger.info(f"_dispatch_to_all: Token='{token}', UniqueId='{unique_id}', body keys: {list(body.keys())}")
    
    # Определяем тип события из имени handler'а
    event_type = "unknown"
    handler_name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
    if "hangup" in handler_name:
        event_type = "hangup"
    elif "bridge_create" in handler_name:
        event_type = "bridge_create"
    elif "bridge_leave" in handler_name:
        event_type = "bridge_leave"
    elif "bridge_destroy" in handler_name:
        event_type = "bridge_destroy"
    elif "new_callerid" in handler_name:
        event_type = "new_callerid"
    elif "bridge" in handler_name:
        event_type = "bridge"
    elif "dial" in handler_name:
        event_type = "dial"
    elif "start" in handler_name:
        event_type = "start"
    logger.info(f"Detected event_type: {event_type} from handler: {handler_name}")
    
    # ────────────────────────────────────────────────────────────────────────────────
    # Call Tracer: Универсальное логирование событий для ВСЕХ юнитов
    # ────────────────────────────────────────────────────────────────────────────────
    try:
        enterprise_number = await _get_enterprise_number_by_token(token)
        if enterprise_number:
            # Логируем AST событие через модуль call_tracer
            log_asterisk_event(enterprise_number, event_type, unique_id, body)
            # Передаём enterprise_number в body для использования в обработчиках
            body["_enterprise_number"] = enterprise_number
    except Exception as e:
        logger.warning(f"Call tracer logging failed for token {token}: {e}")
    
    # Нормализуем номер по правилу на линии (если задано)
    await _apply_incoming_transform_if_any(body)

    # Сохраняем событие в PostgreSQL
    from app.services.events import save_asterisk_event, mark_telegram_sent
    await save_asterisk_event(event_type, unique_id, token, body)
    
    print(f"🔥 BEFORE _get_bot_and_recipients for token={token}")
    logger.info(f"🔥 BEFORE _get_bot_and_recipients for token={token}")
    
    try:
        bot_token, tg_ids = await _get_bot_and_recipients(token)
        print(f"🔥 AFTER _get_bot_and_recipients: bot_token={bot_token}, tg_ids={tg_ids}")
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
    
    # 🔗 Для bridge событий проверяем дубликаты по BridgeUniqueid ОДИН РАЗ
    if event_type == "bridge":
        from app.services.calls.bridge import sent_bridges
        import time
        bridge_id = body.get("BridgeUniqueid", "")
        if bridge_id and bridge_id in sent_bridges:
            time_since_sent = time.time() - sent_bridges[bridge_id]
            logger.info(f"[_dispatch_to_all] Skipping bridge {bridge_id} - already sent {time_since_sent:.1f}s ago (duplicate)")
            return {"delivered": [{"status": "skipped", "reason": "duplicate bridge"}]}
        # Помечаем bridge как отправленный
        if bridge_id:
            sent_bridges[bridge_id] = time.time()
            logger.info(f"[_dispatch_to_all] Marked bridge {bridge_id} as sent")
    
    # 🎯 ОПТИМИЗАЦИЯ: Подготовка данных ДО цикла по подписчикам
    # Для start/dial/bridge/hangup делаем enrichment ОДИН РАЗ
    if event_type in ["start", "dial", "bridge", "hangup"]:
        try:
            from app.services.metadata_client import metadata_client, extract_line_id_from_exten
            from app.services.calls.utils import is_internal_number
            
            # Получаем enterprise_number
            enterprise_number = await _get_enterprise_number_by_token(token)
            
            if enterprise_number and enterprise_number != "0000":
                internal_phone = None
                external_phone = None
                line_id = None
                
                # Извлекаем параметры в зависимости от типа события
                if event_type == "start":
                    # START: извлекаем линию и внешний номер для обогащения
                    trunk = body.get("Trunk", "")
                    line_id = extract_line_id_from_exten(trunk)
                    
                    # Извлекаем внешний номер для обогащения именем клиента
                    raw_phone = body.get("Phone", "") or body.get("CallerIDNum", "") or ""
                    if raw_phone and not is_internal_number(raw_phone):
                        external_phone = raw_phone
                
                elif event_type == "dial":
                    call_type = int(body.get("CallType", 0))
                    raw_phone = body.get("Phone", "") or body.get("CallerIDNum", "") or ""
                    exts = body.get("Extensions", [])
                    trunk = body.get("Trunk", "")
                    
                    line_id = extract_line_id_from_exten(trunk)
                    external_phone = raw_phone if call_type != 2 else None
                    
                    # Ищем внутренний номер
                    if exts:
                        for ext in exts:
                            if is_internal_number(str(ext)):
                                internal_phone = str(ext)
                                break
                    
                    if not internal_phone:
                        caller_id = body.get("CallerIDNum", "")
                        if is_internal_number(caller_id):
                            internal_phone = caller_id
                
                elif event_type == "bridge":
                    caller = body.get("CallerIDNum", "")
                    connected = body.get("ConnectedLineNum", "")
                    
                    caller_internal = is_internal_number(caller)
                    connected_internal = is_internal_number(connected)
                    
                    if caller_internal:
                        internal_phone = caller
                        external_phone = connected if not connected_internal else None
                    elif connected_internal:
                        internal_phone = connected
                        external_phone = caller
                    
                    # Извлекаем trunk
                    trunk = body.get("Trunk", "")
                    if not trunk:
                        channel = body.get("Channel", "")
                        if channel and "/" in channel and "-" in channel:
                            parts = channel.split("/")
                            if len(parts) > 1:
                                trunk = parts[1].split("-")[0]
                    line_id = trunk
                
                elif event_type == "hangup":
                    call_type = int(body.get("CallType", 0))
                    caller = body.get("Phone", "")
                    exts = body.get("Extensions", [])
                    trunk = body.get("Trunk", "")
                    
                    line_id = extract_line_id_from_exten(trunk)
                    
                    if call_type == 0:  # Входящий
                        external_phone = caller
                        # Ищем внутренний номер в Extensions
                        if exts:
                            for ext in exts:
                                if ext and is_internal_number(str(ext)):
                                    internal_phone = str(ext)
                                    break
                        
                        # Если не нашли, ищем в старой таблице call_events
                        if not internal_phone:
                            try:
                                from app.services.postgres import get_pool
                                pool = await get_pool()
                                if pool:
                                    async with pool.acquire() as connection:
                                        query = """
                                            SELECT raw_data->'Extensions' as extensions
                                            FROM call_events
                                            WHERE unique_id = $1
                                              AND event_type = 'dial'
                                            ORDER BY event_timestamp ASC
                                            LIMIT 1
                                        """
                                        result = await connection.fetchrow(query, unique_id)
                                        if result and result['extensions']:
                                            try:
                                                import json
                                                extensions = json.loads(str(result['extensions']))
                                                for ext in extensions:
                                                    if ext and is_internal_number(str(ext)):
                                                        internal_phone = str(ext)
                                                        logger.info(f"✅ Found internal_phone '{internal_phone}' from call_events for hangup")
                                                        break
                                            except Exception as parse_e:
                                                logger.error(f"Failed to parse extensions: {parse_e}")
                            except Exception as e:
                                logger.error(f"Error finding internal_phone for hangup: {e}")
                    
                    elif call_type == 1:  # Исходящий
                        external_phone = caller
                        if exts:
                            for ext in exts:
                                if ext and is_internal_number(str(ext)):
                                    internal_phone = str(ext)
                                    break
                        
                        # Если не нашли, ищем в старой таблице call_events
                        if not internal_phone:
                            try:
                                from app.services.postgres import get_pool
                                pool = await get_pool()
                                if pool:
                                    async with pool.acquire() as connection:
                                        query = """
                                            SELECT raw_data->'Extensions' as extensions
                                            FROM call_events
                                            WHERE unique_id = $1
                                              AND event_type = 'dial'
                                            ORDER BY event_timestamp ASC
                                            LIMIT 1
                                        """
                                        result = await connection.fetchrow(query, unique_id)
                                        if result and result['extensions']:
                                            try:
                                                import json
                                                extensions = json.loads(str(result['extensions']))
                                                for ext in extensions:
                                                    if ext and is_internal_number(str(ext)):
                                                        internal_phone = str(ext)
                                                        logger.info(f"✅ Found internal_phone '{internal_phone}' from call_events for hangup")
                                                        break
                                            except Exception as parse_e:
                                                logger.error(f"Failed to parse extensions: {parse_e}")
                            except Exception as e:
                                logger.error(f"Error finding internal_phone for hangup: {e}")
                
                # Делаем enrichment ОДИН РАЗ для всех подписчиков
                # Для START достаточно только line_id
                if internal_phone or external_phone or line_id:
                    enriched_data = await metadata_client.enrich_message_data(
                        enterprise_number=enterprise_number,
                        internal_phone=internal_phone,
                        external_phone=external_phone,
                        line_id=line_id,
                        short_names=False
                    )
                    body["_enriched_data"] = enriched_data
                    body["_internal_phone"] = internal_phone
                    body["_external_phone"] = external_phone
                    body["_line_id"] = line_id
                    logger.info(f"✅ Enriched data ONCE for all subscribers: {enriched_data}")
        except Exception as e:
            import traceback
            logger.error(f"Failed to prepare enrichment data: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
    
    telegram_success = False

    # Помечаем что вызов идёт из _dispatch_to_all (для корректной работы should_send_bridge)
    body["_from_dispatch_to_all"] = True
    
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


