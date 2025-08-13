# -*- coding: utf-8 -*-
import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query, status, Body, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncpg
import logging
from typing import Dict, Optional, List, Any
import json
import jwt
from datetime import datetime
import random
import string
from pydantic import BaseModel
import time
import os
import uuid
import asyncio
import re
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import shutil
import httpx
import subprocess

from app.config import JWT_SECRET_KEY, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT

# A set of reserved numbers that cannot be assigned.
RESERVED_INTERNAL_NUMBERS = {301, 302, 555}

# --- FollowMe support: ensure columns exist ---
async def _ensure_followme_columns() -> None:
    try:
        conn = await get_db_connection()
        if not conn:
            return
        try:
            await conn.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS follow_me_number INTEGER,
                ADD COLUMN IF NOT EXISTS follow_me_enabled BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS follow_me_steps JSONB
                """
            )
        finally:
            await conn.close()
    except Exception as e:
        logging.error(f"Failed to ensure follow_me columns: {e}")

# Определяем корневую директорию проекта
# __file__ -> enterprise_admin_service.py
# .parent -> /
# .parent -> /root/asterisk-webhook (проект)
PROJECT_ROOT = Path(__file__).resolve().parent

# ——————————————————————————————————————————————————————————————————————————
# Pydantic Models (defined locally to avoid import issues)
# ——————————————————————————————————————————————————————————————————————————

class UserUpdate(BaseModel):
    email: str
    last_name: str
    first_name: str
    patronymic: Optional[str] = None
    personal_phone: Optional[str] = None
    internal_phones: Optional[List[str]] = None
    # Поля ролей
    is_admin: bool = False
    is_employee: bool = True
    is_marketer: bool = False
    is_spec1: bool = False
    is_spec2: bool = False
    # FollowMe
    follow_me_number: Optional[int] = None
    follow_me_enabled: bool = False
    follow_me_steps: Optional[Any] = None

class UserCreate(UserUpdate):
    pass

class CreateLineRequest(BaseModel):
    phone_number: str
    password: str

class MusicFileCreate(BaseModel):
    display_name: str
    file_type: str

class SipLineCreate(BaseModel):
    provider_id: int
    line_name: str
    password: str
    prefix: Optional[str] = None

class SipLineUpdate(SipLineCreate):
    pass

class DepartmentCreate(BaseModel):
    name: str
    number: int

class DepartmentUpdate(BaseModel):
    name: str
    number: int
    members: Optional[List[int]] = None # Список ID внутренниx номеров (internal_phone_id)

# ——————————————————————————————————————————————————————————————————————————
# Basic Configuration
# ——————————————————————————————————————————————————————————————————————————

app = FastAPI()

@app.middleware("http")
async def log_all_requests_middleware(request: Request, call_next):
    """
    Middleware для логирования всех входящих HTTP запросов.
    """
    start_time = time.time()
    logger.info(f"Получен запрос: {request.method} {request.url}")

    response = await call_next(request)

    process_time = time.time() - start_time
    logger.info(f"Запрос обработан за {process_time:.4f} сек. Статус: {response.status_code}. Адрес: {request.method} {request.url}")

    return response

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/music", StaticFiles(directory="music"), name="music")

templates = Jinja2Templates(directory="templates", auto_reload=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация БД при старте сервиса
@app.on_event("startup")
async def _startup_init_followme():
    await _ensure_followme_columns()
    # Инициализация таблиц магазинов
    await _ensure_shops_tables()

async def _ensure_shops_tables() -> None:
    """Создаёт таблицы shops и shop_lines при старте, если их ещё нет."""
    try:
        conn = await get_db_connection()
        if not conn:
            return
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shops (
                    id SERIAL PRIMARY KEY,
                    enterprise_number TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shop_lines (
                    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
                    gsm_line_id INTEGER NOT NULL,
                    enterprise_number TEXT NOT NULL,
                    PRIMARY KEY (shop_id, gsm_line_id)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shop_sip_lines (
                    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
                    sip_line_id INTEGER NOT NULL,
                    enterprise_number TEXT NOT NULL,
                    PRIMARY KEY (shop_id, sip_line_id)
                );
                """
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Failed to ensure shops tables: {e}")

# Add a handler for log_action.txt
log_action_handler = logging.FileHandler("log_action.txt", mode='a')
log_action_handler.setLevel(logging.INFO)
log_action_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(log_action_handler)

# ——————————————————————————————————————————————————————————————————————————
# Database Functions
# ——————————————————————————————————————————————————————————————————————————

DB_CONFIG = {
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg==",
    "host": "localhost",
    "port": 5432,
    "database": "postgres"
}

async def get_db_connection():
    try:
        conn = await asyncpg.connect(user=DB_CONFIG["user"], password=DB_CONFIG["password"], database=DB_CONFIG["database"], host=DB_CONFIG["host"], port=DB_CONFIG["port"])
        return conn
    except asyncpg.PostgresError as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

async def get_enterprise_by_number_from_db(number: str) -> Optional[Dict]:
    conn = await get_db_connection()
    if conn:
        try:
            row = await conn.fetchrow("SELECT id, number, name FROM enterprises WHERE number = $1", number)
            return dict(row) if row else None
        finally:
            await conn.close()
    return None

async def get_file_path_from_db(file_id: int, enterprise_number: str) -> Optional[str]:
    conn = await get_db_connection()
    if not conn: return None
    try:
        path = await conn.fetchval(
            "SELECT file_path FROM music_files WHERE id = $1 AND enterprise_number = $2",
            file_id, enterprise_number
        )
        return path
    finally:
        await conn.close()

async def _generate_sip_addproviders_conf(conn, enterprise_number: str) -> str:
    """
    Генерирует содержимое файла sip_addproviders.conf на основе данных из БД.
    Порядок: gsm-линии, внутренние номера, sip-линии.
    """
    content_parts = []
    
    # 1. GSM-линии (сортировка по line_id)
    try:
        gsm_lines = await conn.fetch(
            "SELECT line_id FROM gsm_lines WHERE enterprise_number = $1 ORDER BY line_id",
            enterprise_number
        )
        for line in gsm_lines:
            context = f"""
[{line['line_id']}]
host=dynamic
type=peer
secret=4bfX5XuefNp3aksfhj232
callgroup=1
pickupgroup=1
disallow=all
allow=ulaw
context=from-out-office
directmedia=no
nat=force_rport,comedia
qualify=8000
insecure=invite
defaultuser=s
""".strip()
            content_parts.append(context)
    except Exception as e:
        logger.error(f"Ошибка при получении GSM-линий для конфига: {e}", exc_info=True)


    # 2. Внутренние линии (сортировка по номеру)
    try:
        internal_lines = await conn.fetch(
            "SELECT phone_number, password FROM user_internal_phones WHERE enterprise_number = $1 ORDER BY phone_number::integer",
            enterprise_number
        )
        for line in internal_lines:
            context = f"""
[{line['phone_number']}]
host=dynamic
type=friend
secret={line['password']}
callgroup=1
pickupgroup=1
disallow=all
allow=ulaw
context=inoffice
directmedia=no
nat=force_rport,comedia
qualify=8000
insecure=invite
callerid={line['phone_number']}
defaultuser={line['phone_number']}
""".strip()
            content_parts.append(context)
    except Exception as e:
        logger.error(f"Ошибка при получении внутренних линий для конфига: {e}", exc_info=True)


    # 3. SIP-линии (сортировка по id)
    try:
        sip_lines = await conn.fetch(
            "SELECT line_name, info FROM sip_unit WHERE enterprise_number = $1 ORDER BY id",
            enterprise_number
        )
        for line in sip_lines:
            # Тело контекста берется напрямую из поля 'info'
            context = f"[{line['line_name']}]\n{line['info']}".strip()
            content_parts.append(context)
    except Exception as e:
        logger.error(f"Ошибка при получении SIP-линий для конфига: {e}", exc_info=True)

    return "\n\n".join(content_parts)

async def _regenerate_sip_config_via_plan_service(enterprise_number: str) -> dict:
    """Вызывает план-сервис для генерации и развертывания sip_addproviders.conf"""
    try:
        async with httpx.AsyncClient() as client:
            plan_service_url = f"http://localhost:8006/generate_sip_config"
            response = await client.post(
                plan_service_url, 
                json={"enterprise_id": enterprise_number}, 
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            
            # Обрабатываем ответ от план-сервиса
            deployment_info = result.get("deployment", {})
            deployment_success = deployment_info.get("success", False)
            deployment_message = deployment_info.get("message", "Информация о развертывании недоступна")
            
            if deployment_success:
                logger.info(f"Успешная генерация и развертывание SIP конфига для предприятия {enterprise_number}")
                return {
                    "status": "success", 
                    "detail": "Конфигурация SIP обновлена"
                }
            else:
                logger.warning(f"SIP конфиг сгенерирован, но развертывание не удалось для предприятия {enterprise_number}: {deployment_message}")
                return {
                    "status": "warning", 
                    "detail": "Нет связи с АТС, повторите попытку позже"
                }
                
    except httpx.TimeoutException:
        logger.error(f"Timeout при вызове план-сервиса для SIP конфига предприятия {enterprise_number}")
        return {"status": "error", "detail": "Нет связи с АТС, повторите попытку позже"}
    except httpx.RequestError as e:
        logger.error(f"Ошибка при вызове план-сервиса для SIP конфига предприятия {enterprise_number}: {e}")
        return {"status": "error", "detail": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при генерации SIP конфига для предприятия {enterprise_number}: {e}", exc_info=True)
        return {"status": "error", "detail": "Нет связи с АТС, повторите попытку позже"}

async def _deploy_single_audio_file_to_asterisk(enterprise_number: str, file_path: str, file_type: str, internal_filename: str) -> dict:
    """Деплой одного аудиофайла на удаленный хост"""
    try:
        async with httpx.AsyncClient() as client:
            if file_type in ["start", "hold"]:
                # Развертываем файл через план-сервис
                plan_service_url = f"http://localhost:8006/deploy_audio_files"
                response = await client.post(
                    plan_service_url, 
                    json={"enterprise_id": enterprise_number}, 
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                
                deployment_info = result.get("deployment", {})
                deployment_success = deployment_info.get("success", False)
                deployment_message = deployment_info.get("message", "Неизвестная ошибка")
                
                logging.info(f"Audio file deployment result for {enterprise_number}: {deployment_message}")
                return {"success": deployment_success, "message": deployment_message}
            
            else:
                return {"success": True, "message": "Тип файла не требует развертывания"}
        
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error during audio file deployment to plan service: {e}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except httpx.TimeoutException:
        logging.error(f"Timeout during audio file deployment to plan service")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error during audio file deployment: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def _deploy_all_audio_files_to_asterisk(enterprise_number: str) -> dict:
    """Деплой всех аудиофайлов предприятия на удаленный хост (кнопка Обновить)"""
    try:
        async with httpx.AsyncClient() as client:
            plan_service_url = f"http://localhost:8006/deploy_audio_files"
            response = await client.post(
                plan_service_url, 
                json={"enterprise_id": enterprise_number}, 
                timeout=60.0  # Больший timeout для полного развертывания
            )
            response.raise_for_status()
            result = response.json()
            
            deployment_info = result.get("deployment", {})
            deployment_success = deployment_info.get("success", False)
            deployment_message = deployment_info.get("message", "Неизвестная ошибка")
            
            logging.info(f"Full audio deployment result for {enterprise_number}: {deployment_message}")
            return {"success": deployment_success, "message": deployment_message}
        
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error during full audio deployment to plan service: {e}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except httpx.TimeoutException:
        logging.error(f"Timeout during full audio deployment to plan service")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error during full audio deployment: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def _generate_musiconhold_conf(conn, enterprise_number: str) -> str:
    """
    Генерирует содержимое файла musiconhold.conf на основе данных из БД.
    """
    base_content = """
;
; Music on Hold -- Sample Configuration
;
[general]
;cachertclasses=yes
[default]
mode=files
directory=moh
""".strip()

    hold_files = await conn.fetch(
        "SELECT internal_filename FROM music_files WHERE enterprise_number = $1 AND file_type = 'hold'",
        enterprise_number
    )

    dynamic_parts = []
    for file in hold_files:
        if file['internal_filename'] and file['internal_filename'].endswith('.wav'):
            context_name = file['internal_filename'][:-4] # Убираем .wav
            part = f"""
[{context_name}]
mode=files
directory={context_name}
sort=random
""".strip()
            dynamic_parts.append(part)

    full_content = base_content + "\n\n" + "\n\n".join(dynamic_parts)
    return full_content

async def _generate_and_write_sip_config(conn, enterprise_number: str):
    """
    Генерирует и записывает конфигурационный файл sip_addproviders.conf на основе данных из БД.
    """
    try:
        config_dir = Path(f"music/{enterprise_number}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "sip_addproviders.conf"
        
        # Генерируем полное содержимое файла
        full_config_content = await _generate_sip_addproviders_conf(conn, enterprise_number)
        
        with open(config_path, "w") as f:
            f.write(full_config_content)
        logger.info(f"Конфигурационный файл '{config_path}' успешно создан/обновлен для предприятия {enterprise_number}.")
    except Exception as e:
        logger.error(f"Не удалось создать/обновить конфигурационный файл: {e}", exc_info=True)

# ——————————————————————————————————————————————————————————————————————————
# Authentication
# ——————————————————————————————————————————————————————————————————————————

@app.get("/", response_class=HTMLResponse)
async def root_login_form(request: Request):
    return templates.TemplateResponse("enterprise_admin/login.html", {"request": request, "error": None})

@app.get("/auth/{token}", response_class=RedirectResponse)
async def auth_by_token(token: str):
    logger.info(f"Попытка аутентификации с токеном: {token[:15]}...")
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        logger.info(f"Токен успешно декодирован. Payload: {payload}")

        if not payload.get("is_admin"):
            logger.warning(f"Токен не является админским. Payload: {payload}")
            raise HTTPException(status_code=403, detail="Not an admin token")
        
        enterprise_number = payload["sub"]
        logger.info(f"Номер предприятия из токена: {enterprise_number}")

        session_token = f"session_admin_{datetime.utcnow().timestamp()}_{random.random()}"

        conn = await get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для создания сессии.")
            raise HTTPException(status_code=500, detail="DB Connection failed")
        
        try:
            logger.info(f"Создание сессии для предприятия {enterprise_number}...")
            await conn.execute("INSERT INTO sessions (session_token, enterprise_number) VALUES ($1, $2)", session_token, enterprise_number)
            logger.info(f"Сессия успешно создана. Токен сессии: {session_token[:15]}...")
        finally:
            await conn.close()

        response = RedirectResponse(url=f"/enterprise/{enterprise_number}/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value=session_token, httponly=True, samesite="lax", max_age=48*3600, secure=True) # Set secure=True in production
        logger.info(f"Перенаправление на дашборд предприятия {enterprise_number}.")
        return response
    except jwt.ExpiredSignatureError:
        logger.warning(f"Истек срок действия токена: {token[:15]}...")
        return RedirectResponse(url="/?error=invalid_token")
    except jwt.InvalidTokenError:
        logger.error(f"Невалидный токен: {token[:15]}...")
        return RedirectResponse(url="/?error=invalid_token")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при аутентификации по токену: {e}", exc_info=True)
        return RedirectResponse(url="/?error=unexpected_error")

async def get_current_enterprise(request: Request) -> str:
    session_token = request.cookies.get("session_token")
    if not session_token:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/"})
    
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection error")

    try:
        row = await conn.fetchrow("SELECT enterprise_number FROM sessions WHERE session_token = $1", session_token)
        if not row:
            response = RedirectResponse(url="/")
            response.delete_cookie("session_token")
            raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/"})
        return row['enterprise_number']
    finally:
        await conn.close()

# ——————————————————————————————————————————————————————————————————————————
# API для операторов
# ——————————————————————————————————————————————————————————————————————————

@app.get("/api/mobiles", response_class=JSONResponse)
async def get_mobiles():
    """
    API для получения списка мобильных операторов с их шаблонами.
    """
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Ошибка подключения к базе данных")
    
    try:
        rows = await conn.fetch("SELECT id, name, shablon FROM mobile ORDER BY id")
        mobiles = [dict(row) for row in rows]
        return mobiles
    except Exception as e:
        logger.error(f"Ошибка при получении данных операторов: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при получении данных операторов")
    finally:
        await conn.close()

# ——————————————————————————————————————————————————————————————————————————
# Enterprise Admin Dashboard & API
# ——————————————————————————————————————————————————————————————————————————

@app.get("/enterprise/{enterprise_number}/dashboard", response_class=HTMLResponse)
async def enterprise_dashboard(request: Request, enterprise_number: str):
    logger.info(f"DASHBOARD: Запрос дашборда для предприятия {enterprise_number}")
    # Убрана проверка current_enterprise для доступа из user авторизации
    enterprise = await get_enterprise_by_number_from_db(enterprise_number)
    if not enterprise:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предприятие не найдено")
    
    # Получаем данные пользователя
    user = None
    session_token = request.cookies.get("session_token")
    logger.info(f"DASHBOARD: session_token = {session_token}")
    
    if session_token:
        conn = await get_db_connection()
        if conn:
            try:
                # Сначала ищем в user_sessions (user авторизация)
                user_row = await conn.fetchrow("""
                    SELECT u.id, u.first_name, u.last_name, u.email, u.enterprise_number
                    FROM user_sessions s 
                    JOIN users u ON s.user_id = u.id 
                    WHERE s.session_token = $1 AND s.expires_at > NOW()
                """, session_token)
                
                if user_row:
                    user = dict(user_row)
                    logger.info(f"DASHBOARD: Найден пользователь из user_sessions: {user}")
                else:
                    # Если не найден в user_sessions, ищем в sessions (super admin)
                    logger.info(f"DASHBOARD: Пользователь не найден в user_sessions, проверяем sessions (super admin)")
                    session_row = await conn.fetchrow("""
                        SELECT session_token FROM sessions 
                        WHERE session_token = $1 AND created_at > NOW() - INTERVAL '24 hours'
                    """, session_token)
                    
                    if session_row:
                        # Для super admin'а - берем данные пользователя по enterprise_number
                        admin_user_row = await conn.fetchrow("""
                            SELECT u.id, u.first_name, u.last_name, u.email, u.enterprise_number
                            FROM users u 
                            WHERE u.enterprise_number = $1 AND u.is_admin = true
                            LIMIT 1
                        """, enterprise_number)
                        
                        if admin_user_row:
                            user = dict(admin_user_row)
                            logger.info(f"DASHBOARD: Найден admin пользователь из sessions: {user}")
                        else:
                            logger.info(f"DASHBOARD: Admin пользователь не найден для предприятия {enterprise_number}")
                    else:
                        logger.info(f"DASHBOARD: Токен не найден ни в user_sessions, ни в sessions")
            except Exception as e:
                logger.error(f"DASHBOARD: Ошибка при получении пользователя: {e}")
            finally:
                await conn.close()
    else:
        logger.info("DASHBOARD: session_token отсутствует в cookies")
    
    logger.info(f"DASHBOARD: Передаю в шаблон: enterprise={enterprise}, user={user}")
    
    return templates.TemplateResponse("enterprise_admin/dashboard.html", {
        "request": request, 
        "enterprise": enterprise,
        "user": user
    })

@app.get("/enterprise/{enterprise_number}/users", response_class=JSONResponse)
async def get_enterprise_users(enterprise_number: str):
    # Убрана проверка current_enterprise для доступа из суперадмина
    
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        sql_query = """
            SELECT * FROM (
                WITH personal_incoming_schemas AS (
                    SELECT 
                        user_id, 
                        array_agg(schema_name) AS incoming_schema_names
                    FROM user_personal_phone_incoming_assignments
                    WHERE enterprise_number = $1
                    GROUP BY user_id
                ),
                internal_incoming_schemas AS (
                    SELECT 
                        internal_phone_id, 
                        array_agg(schema_name) AS incoming_schema_names
                    FROM user_internal_phone_incoming_assignments
                    WHERE enterprise_number = $1
                    GROUP BY internal_phone_id
                )
                SELECT 
                    u.id AS user_id,
                    u.first_name,
                    u.last_name,
                    u.patronymic,
                    TRIM(COALESCE(u.last_name, '') || ' ' || COALESCE(u.first_name, '')) AS full_name,
                    u.email,
                    u.personal_phone AS phone_number,
                    'user' AS line_type,
                    COALESCE(pis.incoming_schema_names, ARRAY[]::varchar[]) AS incoming_schema_names,
                    NULL AS outgoing_schema_name,
                    NULL AS ip_registration,
                    NULL AS role,
                    NULL AS department
                FROM users u
                LEFT JOIN personal_incoming_schemas pis ON u.id = pis.user_id
                WHERE u.enterprise_number = $1 AND u.personal_phone IS NOT NULL

                UNION ALL

                SELECT 
                    uip.user_id,
                    u.first_name,
                    u.last_name,
                    u.patronymic,
                    TRIM(COALESCE(u.last_name, '') || ' ' || COALESCE(u.first_name, '')) AS full_name,
                    u.email,
                    uip.phone_number,
                    'internal' AS line_type,
                    COALESCE(iis.incoming_schema_names, ARRAY[]::varchar[]) AS incoming_schema_names,
                    ds.schema_name AS outgoing_schema_name,
                    NULL AS ip_registration,
                    CASE 
                        WHEN u.is_admin THEN 'Администратор'
                        WHEN u.is_employee THEN 'Сотрудник'
                        WHEN u.is_marketer THEN 'Маркетолог'
                        WHEN u.is_spec1 THEN 'Spec1'
                        WHEN u.is_spec2 THEN 'Spec2'
                        ELSE 'Не указано'
                    END AS role,
                    NULL AS department
                FROM user_internal_phones uip
                LEFT JOIN users u ON uip.user_id = u.id
                LEFT JOIN dial_schemas ds ON uip.outgoing_schema_id = ds.schema_id
                LEFT JOIN internal_incoming_schemas iis ON uip.id = iis.internal_phone_id
                WHERE uip.enterprise_number = $1
            ) AS combined_users
            ORDER BY 
                last_name, 
                first_name, 
                CASE 
                    WHEN line_type = 'internal' THEN 1
                    WHEN line_type = 'user' THEN 2
                    ELSE 3
                END,
                phone_number;
        """
        
        users_and_lines = await conn.fetch(sql_query, enterprise_number)
        
        # Группируем линии по пользователям
        users_data = {}
        for record in users_and_lines:
            user_id = record['user_id']
            if user_id not in users_data:
                users_data[user_id] = {
                    "user_id": user_id,
                    "full_name": record['full_name'],
                    "email": record['email'],
                    "role": record.get('role', 'Не указано'),
                    "department": record.get('department', ''),
                    "lines": []
                }
            
            users_data[user_id]['lines'].append({
                "phone_number": record['phone_number'],
                "line_type": record['line_type'],
                "incoming_schema_names": record['incoming_schema_names'] or [],
                "outgoing_schema_name": record['outgoing_schema_name'],
                "ip_registration": record.get('ip_registration'),
                "role": record.get('role', 'Не указано'),
                "department": record.get('department', '')
            })

        logger.info(f"Найдено {len(users_data)} пользователей для предприятия {enterprise_number}")
        return JSONResponse(content=list(users_data.values()))
        
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей и линий для предприятия {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()

@app.get("/enterprise/{enterprise_number}/internal-phones/all", response_class=JSONResponse)
async def get_all_internal_phones(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        SELECT p.id, p.phone_number, p.user_id, (u.first_name || ' ' || u.last_name) AS manager_name
        FROM user_internal_phones p LEFT JOIN users u ON p.user_id = u.id
        WHERE p.enterprise_number = $1 ORDER BY (CASE WHEN p.phone_number ~ '^[0-9]+$' THEN p.phone_number::int END)
        """
        rows = await conn.fetch(query, enterprise_number)
        return JSONResponse(content=[dict(row) for row in rows])
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/internal-phones/next-available", response_class=JSONResponse)
async def get_next_available_number(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        rows = await conn.fetch("SELECT phone_number FROM user_internal_phones WHERE enterprise_number = $1 AND phone_number ~ '^[0-9]+$'", enterprise_number)
        existing_numbers = {int(row['phone_number']) for row in rows if row['phone_number']}
        
        for num in range(150, 900):
            if num not in existing_numbers and num not in RESERVED_INTERNAL_NUMBERS:
                return JSONResponse(content={"next_number": num})
        for num in range(100, 150):
            if num not in existing_numbers and num not in RESERVED_INTERNAL_NUMBERS:
                return JSONResponse(content={"next_number": num})
        return JSONResponse(content={"error": "No available numbers in range 100-899"}, status_code=404)
    finally:
        await conn.close()

@app.post("/enterprise/{enterprise_number}/users", status_code=status.HTTP_201_CREATED)
async def create_user(enterprise_number: str, user_data: UserCreate):
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        async with conn.transaction():
            # Шаг 1: Проверка на дубликаты
            existing_user_by_email = await conn.fetchrow("SELECT id FROM users WHERE email = $1 AND enterprise_number = $2", user_data.email, enterprise_number)
            if existing_user_by_email:
                raise HTTPException(status_code=400, detail="Пользователь с таким email уже существует.")

            if user_data.personal_phone:
                existing_user_by_phone = await conn.fetchrow("SELECT id FROM users WHERE personal_phone = $1 AND enterprise_number = $2", user_data.personal_phone, enterprise_number)
                if existing_user_by_phone:
                    raise HTTPException(status_code=400, detail="Пользователь с таким внешним номером телефона уже существует.")

            # Шаг 2: Создание пользователя
            new_user_id = await conn.fetchval(
                """
                INSERT INTO users (enterprise_number, email, first_name, last_name, patronymic, personal_phone, status,
                                 is_admin, is_employee, is_marketer, is_spec1, is_spec2)
                VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, $8, $9, $10, $11)
                RETURNING id
                """,
                enterprise_number, user_data.email, user_data.first_name, user_data.last_name,
                user_data.patronymic, user_data.personal_phone,
                user_data.is_admin, user_data.is_employee, user_data.is_marketer, user_data.is_spec1, user_data.is_spec2
            )

            # Шаг 3: Привязка внутренних номеров
            if user_data.internal_phones:
                # Сначала отвязываем эти номера от любого другого пользователя (на всякий случай)
                await conn.execute("UPDATE user_internal_phones SET user_id = NULL WHERE enterprise_number = $1 AND phone_number = ANY($2::text[])",
                                   enterprise_number, user_data.internal_phones)
                # Затем привязываем к новому пользователю
                await conn.execute("UPDATE user_internal_phones SET user_id = $1 WHERE enterprise_number = $2 AND phone_number = ANY($3::text[])",
                                   new_user_id, enterprise_number, user_data.internal_phones)

        return {"status": "success", "user_id": new_user_id}
    except asyncpg.exceptions.UniqueViolationError: # Fallback for race conditions
        raise HTTPException(status_code=400, detail="Пользователь с таким email или телефоном уже существует.")
    finally:
        await conn.close()

@app.delete("/enterprise/{enterprise_number}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(enterprise_number: str, user_id: int, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # Проверяем, существует ли пользователь и есть ли у него внешний номер
        user = await conn.fetchrow("SELECT id, personal_phone FROM users WHERE id = $1 AND enterprise_number = $2", user_id, enterprise_number)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

        # Если внешний номер есть, проверяем его использование в схемах
        if user['personal_phone']:
            assigned_schemas = await conn.fetch(
                """
                SELECT schema_name FROM user_personal_phone_incoming_assignments
                WHERE user_id = $1 AND enterprise_number = $2
                """,
                user_id, enterprise_number
            )
            if assigned_schemas:
                schema_names = [record['schema_name'] for record in assigned_schemas]
                detail_message = f"Невозможно удалить пользователя. Его внешний номер используется во входящих схемах: {', '.join(schema_names)}."
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail_message)

        # Отвязываем внутренние номера от этого пользователя
        await conn.execute("UPDATE user_internal_phones SET user_id = NULL WHERE user_id = $1", user_id)

        # Удаляем пользователя
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)

    except HTTPException:
        # Повторно вызываем HTTPException, чтобы FastAPI обработал его
        raise
    except Exception as e:
        logger.error(f"Ошибка при удалении пользователя {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/users/{user_id}/details_for_edit", response_class=JSONResponse)
async def get_user_details_for_edit(enterprise_number: str, user_id: int, current_enterprise: str = Depends(get_current_enterprise)):
    """
    Эндпоинт, который отдает полную информацию о пользователе 
    для модального окна редактирования.
    """
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        user_query = """
            SELECT id, email, first_name, last_name, patronymic, personal_phone,
                   is_admin, is_employee, is_marketer, is_spec1, is_spec2,
                   follow_me_number, follow_me_enabled, follow_me_steps
            FROM users
            WHERE id = $1 AND enterprise_number = $2;
        """
        user_record = await conn.fetchrow(user_query, user_id, enterprise_number)

        if not user_record:
            raise HTTPException(status_code=404, detail="User not found")

        user_details = dict(user_record)

        # Нормализуем JSONB поле follow_me_steps: отдаём как объект/массив, а не строку
        try:
            if 'follow_me_steps' in user_details and user_details['follow_me_steps'] is not None:
                if isinstance(user_details['follow_me_steps'], str):
                    user_details['follow_me_steps'] = json.loads(user_details['follow_me_steps'])
        except Exception as e:
            logger.warning(f"Failed to parse follow_me_steps JSON for user {user_id}: {e}")

        internal_phones_query = """
            SELECT phone_number 
            FROM user_internal_phones 
            WHERE user_id = $1 AND enterprise_number = $2;
        """
        internal_phones_records = await conn.fetch(internal_phones_query, user_id, enterprise_number)
        
        user_details['internal_phones'] = [record['phone_number'] for record in internal_phones_records]

        # Загрузка отделов, в которых участвуют внутренние номера пользователя
        try:
            departments_query = """
                SELECT DISTINCT d.name, d.number
                FROM departments d
                JOIN department_members dm ON dm.department_id = d.id
                JOIN user_internal_phones uip ON uip.id = dm.internal_phone_id
                WHERE uip.user_id = $1 AND d.enterprise_number = $2
                ORDER BY d.number
            """
            dept_rows = await conn.fetch(departments_query, user_id, enterprise_number)
            user_details['departments'] = [dict(row) for row in dept_rows]
        except Exception as e:
            # Если запрос с отделами упал, не валим весь эндпоинт
            logger.error(f"Failed to load user departments for user {user_id}: {e}", exc_info=True)
            user_details['departments'] = []

        return JSONResponse(content=user_details)

    except Exception as e:
        logger.error(f"Failed to fetch user details for edit for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user details")
    finally:
        if conn:
            await conn.close()

@app.put("/enterprise/{enterprise_number}/users/{user_id}", status_code=status.HTTP_200_OK)
async def update_user(enterprise_number: str, user_id: int, user_data: UserUpdate, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # Explicitly check for duplicate email on another user
        existing_user_by_email = await conn.fetchrow("SELECT id FROM users WHERE email = $1 AND enterprise_number = $2 AND id != $3", user_data.email, enterprise_number, user_id)
        if existing_user_by_email:
            raise HTTPException(status_code=400, detail="Пользователь с таким email уже существует.")

        # Explicitly check for duplicate phone on another user
        if user_data.personal_phone:
            existing_user_by_phone = await conn.fetchrow("SELECT id FROM users WHERE personal_phone = $1 AND enterprise_number = $2 AND id != $3", user_data.personal_phone, enterprise_number, user_id)
            if existing_user_by_phone:
                raise HTTPException(status_code=400, detail="Пользователь с таким внешним номером телефона уже существует.")

        # Валидация follow_me_number: должен быть в диапазоне 100-899, не зарезервирован и не занят как внутренний
        if user_data.follow_me_enabled and user_data.follow_me_number is not None:
            try:
                fm = int(user_data.follow_me_number)
                if not (100 <= fm <= 899) or fm in RESERVED_INTERNAL_NUMBERS:
                    raise ValueError
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Follow Me номер должен быть в диапазоне 100-899 и не входить в {RESERVED_INTERNAL_NUMBERS}")

            # Проверка занятости во внутренних номерах этого предприятия
            exists_internal = await conn.fetchval(
                "SELECT 1 FROM user_internal_phones WHERE enterprise_number = $1 AND phone_number = $2",
                enterprise_number, str(fm)
            )
            if exists_internal:
                raise HTTPException(status_code=400, detail=f"Follow Me номер '{fm}' уже используется как внутренний")

        await conn.execute(
            """UPDATE users SET email = $1, first_name = $2, last_name = $3, patronymic = $4, personal_phone = $5,
               is_admin = $6, is_employee = $7, is_marketer = $8, is_spec1 = $9, is_spec2 = $10,
               follow_me_number = $11, follow_me_enabled = $12, follow_me_steps = $13
               WHERE id = $14 AND enterprise_number = $15""",
            user_data.email, user_data.first_name, user_data.last_name, user_data.patronymic, user_data.personal_phone,
            user_data.is_admin, user_data.is_employee, user_data.is_marketer, user_data.is_spec1, user_data.is_spec2,
            user_data.follow_me_number, user_data.follow_me_enabled, json.dumps(user_data.follow_me_steps) if user_data.follow_me_steps is not None else None,
            user_id, enterprise_number
        )
        if user_data.internal_phones is not None:
            async with conn.transaction():
                await conn.execute("UPDATE user_internal_phones SET user_id = NULL WHERE user_id = $1 AND enterprise_number = $2", user_id, enterprise_number)
                if user_data.internal_phones:
                    await conn.execute("UPDATE user_internal_phones SET user_id = $1 WHERE enterprise_number = $2 AND phone_number = ANY($3::text[])",
                                       user_id, enterprise_number, user_data.internal_phones)

        # Точечная перегенерация диалплана при Follow Me с шагами
        try:
            should_regenerate = False
            if user_data.follow_me_enabled:
                steps_obj = user_data.follow_me_steps
                if isinstance(steps_obj, str):
                    try:
                        steps_obj = json.loads(steps_obj)
                    except Exception:
                        steps_obj = None
                if isinstance(steps_obj, list) and len(steps_obj) > 0:
                    should_regenerate = True
            if should_regenerate:
                async def _regen():
                    try:
                        async with httpx.AsyncClient() as client:
                            plan_service_url = "http://localhost:8006/generate_config"
                            await client.post(plan_service_url, json={"enterprise_id": enterprise_number}, timeout=10.0)
                            logger.info(f"Запущена перегенерация диалплана для предприятия {enterprise_number} (Follow Me обновлен у пользователя {user_id}).")
                    except Exception as e:
                        logger.error(f"Не удалось инициировать перегенерацию диалплана: {e}")
                asyncio.create_task(_regen())
        except Exception as e:
            logger.error(f"Ошибка при попытке инициировать перегенерацию диалплана: {e}")

        return {"status": "success"}
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Пользователь с таким email или телефоном уже существует.")
    finally:
        await conn.close()

@app.post("/enterprise/{enterprise_number}/internal-phones", response_class=JSONResponse)
async def create_internal_line(enterprise_number: str, data: CreateLineRequest, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    try:
        phone_number_int = int(data.phone_number)
        if not (100 <= phone_number_int <= 899) or phone_number_int in RESERVED_INTERNAL_NUMBERS:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Номер должен быть в диапазоне от 100 до 899 и не входить в {RESERVED_INTERNAL_NUMBERS}")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        query = "INSERT INTO user_internal_phones (enterprise_number, phone_number, password) VALUES ($1, $2, $3) RETURNING id"
        new_line_id = await conn.fetchval(query, enterprise_number, data.phone_number, data.password)

        # Регенерация SIP конфига через план-сервис (асинхронно)
        asyncio.create_task(_regenerate_sip_config_via_plan_service(enterprise_number))


        return {"id": new_line_id, "phone_number": data.phone_number, "password": data.password}
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail=f"Внутренний номер '{data.phone_number}' уже существует.")
    except Exception as e:
        logger.error(f"Ошибка при создании внутреннего номера: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/gsm-lines/all", response_class=JSONResponse)
async def get_enterprise_gsm_lines(enterprise_number: str):
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        # Загружаем enterprise.integrations_config и строим карту smart-настроек по линиям
        cfg_row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        integrations_config = (dict(cfg_row).get('integrations_config') if cfg_row else None)
        if isinstance(integrations_config, str):
            try:
                integrations_config = json.loads(integrations_config)
            except Exception:
                integrations_config = None
        smart_lines_map = {}
        if isinstance(integrations_config, dict):
            smart_lines_map = ((integrations_config.get('smart') or {}).get('lines')) or {}

        query = f"""
        SELECT
            g.gateway_name,
            g.custom_boolean_flag,
            gl.id,
            gl.line_id,
            gl.internal_id,
            gl.prefix,
            gl.phone_number,
            gl.line_name,
            gl.in_schema as incoming_schema_name,
            gl.shop,
            gl.slot,
            gl.redirect,
            COALESCE(
                (SELECT array_agg(gosa.schema_name ORDER BY gosa.schema_name)
                 FROM gsm_outgoing_schema_assignments gosa
                 WHERE gosa.gsm_line_id = gl.line_id AND gosa.enterprise_number = $1),
                '{{}}'::text[]
            ) as outgoing_schema_names
        FROM gsm_lines gl
        LEFT JOIN goip g ON gl.goip_id = g.id
        WHERE gl.enterprise_number = $1
        ORDER BY g.gateway_name, gl.id
        """
        rows = await conn.fetch(query, enterprise_number)
        gateways = {}
        for row in rows:
            gateway_name = row['gateway_name'] or 'Без шлюза'
            if gateway_name not in gateways:
                gateways[gateway_name] = {
                    'gateway_name': gateway_name,
                    'gateway_id': 0, # Это поле больше не имеет смысла, но оставим для совместимости
                    'is_main': row['custom_boolean_flag'] if row['custom_boolean_flag'] is not None else False,
                    'lines': []
                }
            if row['id'] is not None:
                sr_key = f"gsm:{row['line_id']}"
                line = {
                    'id': row['id'],
                    'line_id': row['line_id'],
                    'internal_id': row['internal_id'],
                    'prefix': row['prefix'],
                    'phone_number': row['phone_number'],
                    'line_name': row['line_name'],
                    'incoming_schema_name': row['incoming_schema_name'],
                    'outgoing_schema_names': list(row['outgoing_schema_names'] or []),
                    'shop': row['shop'],
                    'slot': row['slot'],
                    'redirect': row['redirect'],
                    'smart': smart_lines_map.get(sr_key)
                }
                gateways[gateway_name]['lines'].append(line)
        return JSONResponse(content=list(gateways.values()))
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/gsm-lines/status", response_class=JSONResponse)
async def get_enterprise_gsm_lines_status(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    """Проверка статуса GSM линий на удаленном хосте Asterisk"""
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    
    try:
        # Получаем IP адрес предприятия
        enterprise_query = "SELECT ip FROM enterprises WHERE number = $1"
        enterprise_row = await conn.fetchrow(enterprise_query, enterprise_number)
        
        if not enterprise_row or not enterprise_row['ip']:
            raise HTTPException(status_code=404, detail="IP адрес предприятия не найден")
        
        host_ip = enterprise_row['ip']
        
        # Получаем все GSM линии предприятия
        lines_query = """
        SELECT line_id, internal_id
        FROM gsm_lines 
        WHERE enterprise_number = $1
        ORDER BY line_id
        """
        lines = await conn.fetch(lines_query, enterprise_number)
        
        if not lines:
            return JSONResponse(content={})
        
        # Выполняем SSH команду на удаленном хосте
        ssh_command = f"sshpass -p '5atx9Ate@pbx' ssh -p 5059 -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{host_ip} 'asterisk -rx \"sip show peers\"'"
        
        process = await asyncio.create_subprocess_shell(
            ssh_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        
        if process.returncode != 0:
            logger.error(f"SSH команда завершилась с ошибкой для {host_ip}: {stderr.decode()}")
            raise HTTPException(status_code=500, detail=f"Ошибка подключения к хосту {host_ip}")
        
        sip_output = stdout.decode('utf-8')
        
        # Парсим вывод sip show peers для поиска GSM линий
        line_status = {}
        
        for line in lines:
            line_id = str(line['line_id']).zfill(7)  # Приводим к формату 0001363
            internal_id = line['internal_id']
            
            # Ищем линию в выводе sip show peers
            ip_address = None
            for sip_line in sip_output.split('\n'):
                if line_id in sip_line:
                    # Реальный формат: "0001363/s                 213.184.245.128                          D  Yes        Yes            5404     OK (9 ms)"
                    # Используем regex для извлечения IP адреса
                    # Ищем паттерн: line_id/s + пробелы + IP адрес + пробелы + остальное
                    pattern = rf'{line_id}/s\s+(\d{{1,3}}\.\d{{1,3}}\.\d{{1,3}}\.\d{{1,3}})'
                    match = re.search(pattern, sip_line)
                    if match and 'OK' in sip_line:
                        ip_address = match.group(1)
                        break
            
            line_status[line['line_id']] = {
                'line_id': line['line_id'],
                'internal_id': internal_id,
                'ip_address': ip_address,
                'status': 'online' if ip_address else 'offline'
            }
        
        return JSONResponse(content=line_status)
        
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail=f"Таймаут при подключении к хосту {host_ip}")
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса GSM линий для {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")
    finally:
        await conn.close()

# ---------------------- Shops (магазины) ----------------------
@app.get("/enterprise/{enterprise_number}/shops", response_class=JSONResponse)
async def list_shops(enterprise_number: str):
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        shops = await conn.fetch(
            "SELECT id, name, created_at FROM shops WHERE enterprise_number = $1 ORDER BY name",
            enterprise_number
        )
        # для каждой — подтянуть список линий
        result = []
        for s in shops:
            lines = await conn.fetch(
                """
                SELECT gl.id, gl.line_id, gl.phone_number, gl.line_name
                FROM shop_lines sl
                JOIN gsm_lines gl ON sl.gsm_line_id = gl.id
                WHERE sl.shop_id = $1 AND sl.enterprise_number = $2
                ORDER BY gl.line_id
                """,
                s['id'], enterprise_number
            )
            sip_lines = await conn.fetch(
                """
                SELECT su.id, su.line_name
                FROM shop_sip_lines ssl
                JOIN sip_unit su ON ssl.sip_line_id = su.id
                WHERE ssl.shop_id = $1 AND ssl.enterprise_number = $2 AND su.enterprise_number = $2
                ORDER BY su.line_name
                """,
                s['id'], enterprise_number
            )
            result.append({
                "id": s['id'], "name": s['name'], "created_at": s['created_at'],
                "lines": [dict(r) for r in lines],
                "sip_lines": [dict(r) for r in sip_lines]
            })
        return result
    finally:
        await conn.close()

@app.post("/enterprise/{enterprise_number}/shops", response_class=JSONResponse)
async def create_shop(enterprise_number: str, data: dict = Body(...)):
    name = (data.get("name") or "").strip()
    line_ids = data.get("line_ids") or []  # ожидаем массив id из gsm_lines.id
    if not name:
        raise HTTPException(status_code=400, detail="Название обязательно")
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        async with conn.transaction():
            shop_id = await conn.fetchval(
                "INSERT INTO shops(enterprise_number, name) VALUES ($1, $2) RETURNING id",
                enterprise_number, name
            )
            if line_ids:
                insert_values = [(shop_id, int(lid), enterprise_number) for lid in line_ids]
                await conn.executemany(
                    "INSERT INTO shop_lines(shop_id, gsm_line_id, enterprise_number) VALUES ($1, $2, $3)",
                    insert_values
                )
        return {"id": shop_id, "name": name, "line_ids": line_ids}
    finally:
        await conn.close()

@app.put("/enterprise/{enterprise_number}/shops/{shop_id}", response_class=JSONResponse)
async def update_shop(enterprise_number: str, shop_id: int, data: dict = Body(...)):
    """Обновление названия магазина (и в перспективе набора линий)."""
    name = data.get("name")
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        if name is not None:
            await conn.execute(
                "UPDATE shops SET name = $1 WHERE id = $2 AND enterprise_number = $3",
                name, shop_id, enterprise_number
            )
        # Возвращаем актуальные данные по магазину
        shop = await conn.fetchrow(
            "SELECT id, name, created_at FROM shops WHERE id = $1 AND enterprise_number = $2",
            shop_id, enterprise_number
        )
        lines = await conn.fetch(
            """
            SELECT gl.id, gl.line_id, gl.phone_number, gl.line_name
            FROM shop_lines sl
            JOIN gsm_lines gl ON sl.gsm_line_id = gl.id
            WHERE sl.shop_id = $1 AND sl.enterprise_number = $2
            ORDER BY gl.line_id
            """,
            shop_id, enterprise_number
        )
        sip_lines = await conn.fetch(
            """
            SELECT su.id, su.line_name
            FROM shop_sip_lines ssl
            JOIN sip_unit su ON ssl.sip_line_id = su.id
            WHERE ssl.shop_id = $1 AND ssl.enterprise_number = $2 AND su.enterprise_number = $2
            ORDER BY su.line_name
            """,
            shop_id, enterprise_number
        )
        return {"id": shop["id"], "name": shop["name"], "lines": [dict(r) for r in lines], "sip_lines": [dict(r) for r in sip_lines]}
    finally:
        await conn.close()

@app.put("/enterprise/{enterprise_number}/shops/{shop_id}/lines", response_class=JSONResponse)
async def set_shop_lines(enterprise_number: str, shop_id: int, data: dict = Body(...)):
    """Полная замена набора линий для магазина."""
    line_ids = data.get("line_ids") or []
    line_ids = [int(x) for x in line_ids]
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM shop_lines WHERE shop_id = $1 AND enterprise_number = $2",
                shop_id, enterprise_number
            )
            if line_ids:
                await conn.executemany(
                    "INSERT INTO shop_lines(shop_id, gsm_line_id, enterprise_number) VALUES ($1,$2,$3)",
                    [(shop_id, lid, enterprise_number) for lid in line_ids]
                )
        return {"success": True, "shop_id": shop_id, "line_ids": line_ids}
    finally:
        await conn.close()

@app.put("/enterprise/{enterprise_number}/shops/{shop_id}/sip-lines", response_class=JSONResponse)
async def set_shop_sip_lines(enterprise_number: str, shop_id: int, data: dict = Body(...)):
    """Полная замена набора SIP-линий для магазина."""
    sip_line_ids = data.get("sip_line_ids") or []
    sip_line_ids = [int(x) for x in sip_line_ids]
    logger.info(f"set_shop_sip_lines: enterprise={enterprise_number} shop_id={shop_id} ids={sip_line_ids}")
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM shop_sip_lines WHERE shop_id = $1 AND enterprise_number = $2",
                shop_id, enterprise_number
            )
            # Эксклюзивная привязка: удаляем эти SIP-линии из других магазинов данного предприятия
            if sip_line_ids:
                await conn.execute(
                    "DELETE FROM shop_sip_lines WHERE enterprise_number = $1 AND shop_id <> $2 AND sip_line_id = ANY($3)",
                    enterprise_number, shop_id, sip_line_ids
                )
            if sip_line_ids:
                await conn.executemany(
                    "INSERT INTO shop_sip_lines(shop_id, sip_line_id, enterprise_number) VALUES ($1,$2,$3)",
                    [(shop_id, lid, enterprise_number) for lid in sip_line_ids]
                )
        saved = await conn.fetch(
            "SELECT sip_line_id FROM shop_sip_lines WHERE shop_id=$1 AND enterprise_number=$2 ORDER BY sip_line_id",
            shop_id, enterprise_number
        )
        saved_ids = [r["sip_line_id"] for r in saved]
        logger.info(f"set_shop_sip_lines saved_ids={saved_ids}")
        return {"success": True, "shop_id": shop_id, "sip_line_ids": saved_ids}
    finally:
        await conn.close()

@app.delete("/enterprise/{enterprise_number}/shops/{shop_id}", response_class=JSONResponse)
async def delete_shop(enterprise_number: str, shop_id: int):
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM shop_lines WHERE shop_id = $1 AND enterprise_number = $2",
                shop_id, enterprise_number
            )
            await conn.execute(
                "DELETE FROM shops WHERE id = $1 AND enterprise_number = $2",
                shop_id, enterprise_number
            )
        return {"success": True}
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/gsm-lines/{line_id}", response_class=JSONResponse)
async def get_gsm_line(enterprise_number: str, line_id: int):
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        query = """
            SELECT id, line_id, internal_id, prefix, phone_number, line_name, in_schema, out_schema, shop, slot, redirect
            FROM gsm_lines
            WHERE id = $1 AND enterprise_number = $2
            """
        row = await conn.fetchrow(query, line_id, enterprise_number)
        if not row:
            raise HTTPException(status_code=404, detail="Линия не найдена")
        data = dict(row)
        # Smart настройки берём из enterprises.integrations_config
        cfg_row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        integrations_config = (dict(cfg_row).get('integrations_config') if cfg_row else None)
        if isinstance(integrations_config, str):
            try:
                integrations_config = json.loads(integrations_config)
            except Exception:
                integrations_config = None
        smart = None
        if isinstance(integrations_config, dict):
            smart = ((integrations_config.get('smart') or {}).get('lines') or {}).get(f"gsm:{data['line_id']}")
        data['smart'] = smart
        return data
    finally:
        await conn.close()

@app.put("/enterprise/{enterprise_number}/gsm-lines/{line_id}", response_class=JSONResponse)
async def update_gsm_line(
    enterprise_number: str,
    line_id: int,
    data: dict = Body(...)
):
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        query = """
            UPDATE gsm_lines
            SET line_name = $1, phone_number = $2, prefix = $3
            WHERE id = $4 AND enterprise_number = $5
            RETURNING id, line_id, internal_id, prefix, phone_number, line_name, in_schema, out_schema, shop, slot, redirect
            """
        params = [
            data.get("line_name"),
            data.get("phone_number"),
            data.get("prefix"),
            line_id,
            enterprise_number,
        ]
        row = await conn.fetchrow(query, *params)
        if not row:
            raise HTTPException(status_code=404, detail="Линия не найдена")
        updated = dict(row)

        # Сохраняем smart в enterprises.integrations_config
        if 'smart' in data:
            sr_key = f"gsm:{updated['line_id']}"
            cfg_row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
            current_cfg = (dict(cfg_row).get('integrations_config') if cfg_row else None)
            if isinstance(current_cfg, str):
                try:
                    current_cfg = json.loads(current_cfg)
                except Exception:
                    current_cfg = None
            if not isinstance(current_cfg, dict):
                current_cfg = {}
            current_cfg.setdefault('smart', {})
            current_cfg['smart'].setdefault('lines', {})
            current_cfg['smart']['lines'][sr_key] = data['smart']
            await conn.execute(
                "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
                json.dumps(current_cfg),
                enterprise_number
            )
            updated['smart'] = data['smart']

        return updated
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/audiofiles", response_class=JSONResponse)
async def get_audio_files(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        WITH schema_file_usage AS (
            -- Извлекаем файлы приветствий
            SELECT
                ds.schema_name,
                (node.value -> 'data' -> 'greetingFile' ->> 'id')::int AS file_id
            FROM
                dial_schemas ds,
                json_array_elements(ds.schema_data -> 'nodes') AS node
            WHERE
                ds.enterprise_id = $1
                AND node.value ->> 'type' = 'greeting'
                AND node.value -> 'data' -> 'greetingFile' ->> 'id' IS NOT NULL

            UNION ALL

            -- Извлекаем файлы музыки на удержании
            SELECT
                ds.schema_name,
                (node.value -> 'data' -> 'holdMusic' ->> 'id')::int AS file_id
            FROM
                dial_schemas ds,
                json_array_elements(ds.schema_data -> 'nodes') AS node
            WHERE
                ds.enterprise_id = $1
                AND node.value ->> 'type' = 'dial'
                AND node.value -> 'data' -> 'holdMusic' ->> 'type' = 'custom'
                AND node.value -> 'data' -> 'holdMusic' ->> 'id' IS NOT NULL
        ),
        aggregated_schemas AS (
            SELECT
                file_id,
                array_agg(DISTINCT schema_name) as used_in_schemas
            FROM schema_file_usage
            GROUP BY file_id
        )
        SELECT
            mf.id,
            mf.display_name,
            mf.file_type,
            mf.file_path,
            mf.original_filename,
            mf.created_at,
            COALESCE(ags.used_in_schemas, '{}'::text[]) as used_in_schemas
        FROM
            music_files mf
        LEFT JOIN aggregated_schemas ags ON mf.id = ags.file_id
        WHERE
            mf.enterprise_number = $1
        ORDER BY
            mf.created_at DESC;
        """
        files = await conn.fetch(query, enterprise_number)
        
        logger.info(f"AUDIO_LIST: Найдено {len(files)} файлов для предприятия {enterprise_number}.")

        # Manually construct the response to handle datetime serialization
        result = [
            {
                "id": file["id"],
                "display_name": file["display_name"],
                "file_type": file["file_type"],
                "file_path": file["file_path"],
                "original_filename": file["original_filename"],
                "created_at": file["created_at"].isoformat(),
                "used_in_schemas": list(file["used_in_schemas"])
            }
            for file in files
        ]
        
        logger.info(f"AUDIO_LIST: Отправка списка файлов клиенту: {result}")
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Could not fetch music files from DB: {e}")
        raise HTTPException(status_code=500, detail="Не удалось получить список аудиофайлов.")
    finally:
        await conn.close()

@app.get("/audiofile/{file_id}")
async def stream_audio_file(file_id: int, current_enterprise: str = Depends(get_current_enterprise)):
    logger.info(f"AUDIO_STREAM: Попытка отдать файл id: {file_id} для предприятия: {current_enterprise}")
    relative_path_from_db = await get_file_path_from_db(file_id, current_enterprise)

    if not relative_path_from_db:
        logger.warning(f"AUDIO_STREAM: Путь к файлу не найден в БД для id: {file_id}, предприятия: {current_enterprise}")
        raise HTTPException(status_code=404, detail="Файл не найден (ошибка поиска в БД)")

    logger.info(f"AUDIO_STREAM: Найден относительный путь в БД: {relative_path_from_db}")
    
    # Преобразуем относительный путь в абсолютный
    absolute_path = PROJECT_ROOT / relative_path_from_db
    logger.info(f"AUDIO_STREAM: Сконструирован абсолютный путь: {absolute_path}")

    if not absolute_path.exists():
        logger.error(f"AUDIO_STREAM: Файл НЕ существует на диске по абсолютному пути: {absolute_path}. CWD: {os.getcwd()}")
        raise HTTPException(status_code=404, detail="Файл не найден (отсутствует на диске)")

    logger.info(f"AUDIO_STREAM: Отдаю файл {absolute_path} с media_type audio/wav")
    return FileResponse(str(absolute_path), media_type="audio/wav")

@app.post("/enterprise/{enterprise_number}/audiofiles", status_code=status.HTTP_201_CREATED)
async def upload_audio_file(
    enterprise_number: str,
    display_name: str = Form(...),
    file_type: str = Form(...),
    file: UploadFile = File(...),
    current_enterprise: str = Depends(get_current_enterprise)
):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if file.content_type not in ["audio/mpeg", "audio/wav", "audio/x-wav"]:
        raise HTTPException(status_code=400, detail="Неверный формат файла. Допускаются .mp3 и .wav")

    # Generate internal filename and path
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    internal_filename = f"{random_chars}.wav" # Always save as wav
    
    base_dir = "music"
    enterprise_dir = os.path.join(base_dir, enterprise_number)
    file_type_dir = os.path.join(enterprise_dir, file_type)
    
    os.makedirs(file_type_dir, exist_ok=True)
    
    final_file_path = os.path.join(file_type_dir, internal_filename)
    temp_file_path = f"/tmp/{uuid.uuid4()}_{file.filename}"

    # Save uploaded file temporarily
    with open(temp_file_path, "wb") as buffer:
        buffer.write(await file.read())

    # Convert file with ffmpeg to wav, 16-bit, 8000Hz, mono
    # Asterisk requires this specific format
    ffmpeg_command = (
        f"ffmpeg -i {temp_file_path} -acodec pcm_s16le -ac 1 -ar 8000 "
        f"{final_file_path}"
    )

    process = await asyncio.create_subprocess_shell(
        ffmpeg_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    # Clean up temp file
    os.remove(temp_file_path)

    if process.returncode != 0:
        logger.error(f"FFmpeg error: {stderr.decode()}")
        raise HTTPException(status_code=500, detail=f"Ошибка конвертации файла: {stderr.decode()}")

    # Insert into database
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        INSERT INTO music_files (enterprise_number, file_type, display_name, internal_filename, original_filename, file_path)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, display_name, file_type, created_at
        """
        new_file_record = await conn.fetchrow(
            query,
            enterprise_number,
            file_type,
            display_name,
            internal_filename,
            file.filename,
            final_file_path
        )
        
        # --- НОВАЯ АРХИТЕКТУРА: ДЕПЛОЙ НА УДАЛЕННЫЙ ХОСТ ---
        # Асинхронно развертываем файл на удаленный хост через план-сервис
        asyncio.create_task(_deploy_single_audio_file_to_asterisk(
            enterprise_number, final_file_path, file_type, internal_filename
        ))
        
        response_data = {
            "id": new_file_record["id"],
            "display_name": new_file_record["display_name"],
            "file_type": new_file_record["file_type"],
            "created_at": new_file_record["created_at"].isoformat()
        }
        return JSONResponse(content=response_data)
    except Exception as e:
        logger.error(f"Could not insert music file into DB: {e}")
        # Clean up created file if DB insert fails
        if os.path.exists(final_file_path):
            os.remove(final_file_path)
        raise HTTPException(status_code=500, detail="Не удалось сохранить информацию о файле в базу данных.")

@app.post("/enterprise/{enterprise_number}/regenerate-musiconhold-conf", status_code=status.HTTP_200_OK)
async def regenerate_musiconhold_conf(
    enterprise_number: str,
    current_enterprise: str = Depends(get_current_enterprise)
):
    """
    Принудительно пересоздает файл musiconhold.conf для предприятия.
    """
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # Вызываем план-сервис для полного развертывания аудиофайлов
        deployment_result = await _deploy_all_audio_files_to_asterisk(enterprise_number)
        
        if deployment_result["success"]:
            logger.info(f"Успешное развертывание всех аудиофайлов для предприятия {enterprise_number}")
            return JSONResponse(content={
                "status": "success", 
                "detail": deployment_result["message"]
            })
        else:
            logger.warning(f"Развертывание аудиофайлов завершено с ошибками для предприятия {enterprise_number}: {deployment_result['message']}")
            return JSONResponse(content={
                "status": "warning", 
                "detail": deployment_result["message"]
            })
            
    except Exception as e:
        logger.error(f"Ошибка при развертывании аудиофайлов для предприятия {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при развертывании аудиофайлов.")
    finally:
        await conn.close()

@app.delete("/enterprise/{enterprise_number}/audiofiles/{file_id}", status_code=status.HTTP_200_OK)
async def delete_audio_file(
    enterprise_number: str,
    file_id: int,
    current_enterprise: str = Depends(get_current_enterprise)
):
    """
    Удаляет аудиофайл, если он не используется ни в одной схеме.
    """
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # 1. Проверяем, существует ли файл
        file_check = await conn.fetchrow(
            "SELECT id, file_path, display_name FROM music_files WHERE id = $1 AND enterprise_number = $2",
            file_id, enterprise_number
        )
        if not file_check:
            raise HTTPException(status_code=404, detail="Аудиофайл не найден")

        # 2. Проверяем, используется ли файл в схемах
        usage_check = await conn.fetchval("""
            SELECT COUNT(*) FROM dial_schemas 
            WHERE enterprise_id = $1 
            AND (
                schema_data::text LIKE '%"type": "custom"%' 
                AND schema_data::text LIKE $2
            )
        """, enterprise_number, f'%"id": {file_id}%')

        if usage_check > 0:
            raise HTTPException(status_code=400, detail="Нельзя удалить файл, который используется в схемах")

        # 3. Удаляем физический файл
        file_path = file_check['file_path']
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Физический файл удален: {file_path}")

        # 4. Удаляем запись из базы данных
        await conn.execute(
            "DELETE FROM music_files WHERE id = $1 AND enterprise_number = $2",
            file_id, enterprise_number
        )

        # 5. Развертываем обновленные аудиофайлы на удаленный хост
        asyncio.create_task(_deploy_all_audio_files_to_asterisk(enterprise_number))

        logger.info(f"Аудиофайл {file_check['display_name']} (ID: {file_id}) успешно удален")
        return JSONResponse(content={"message": f"Аудиофайл '{file_check['display_name']}' успешно удален"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при удалении аудиофайла: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при удалении файла")
    finally:
        await conn.close()

# ——————————————————————————————————————————————————————————————————————————
# Departments Management
# ——————————————————————————————————————————————————————————————————————————

@app.get("/enterprise/{enterprise_number}/departments", response_class=JSONResponse)
async def get_departments(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        SELECT
            d.id,
            d.name,
            d.number
        FROM departments d
        WHERE d.enterprise_number = $1
        ORDER BY d.number
        """
        departments = await conn.fetch(query, enterprise_number)
        return [dict(row) for row in departments]
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/departments/next-available-number", response_class=JSONResponse)
async def get_next_department_number(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    
    try:
        query = "SELECT number FROM departments WHERE enterprise_number = $1"
        rows = await conn.fetch(query, enterprise_number)
        existing_numbers = {row['number'] for row in rows}
        
        for num in range(901, 1000):
            if num not in existing_numbers:
                return JSONResponse(content={"next_number": num})
        
        return JSONResponse(content={"error": "All department numbers are taken"}, status_code=404)
    finally:
        await conn.close()

@app.post("/enterprise/{enterprise_number}/departments", response_class=JSONResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    enterprise_number: str,
    department: DepartmentCreate,
    current_enterprise: str = Depends(get_current_enterprise)
):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        INSERT INTO departments (enterprise_number, name, number)
        VALUES ($1, $2, $3)
        RETURNING id, name, number
        """
        new_department = await conn.fetchrow(query, enterprise_number, department.name, department.number)
        return dict(new_department)
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Отдел с таким именем или номером уже существует.")
    except asyncpg.exceptions.CheckViolationError:
        raise HTTPException(status_code=400, detail="Номер отдела должен быть в диапазоне от 901 до 999.")
    except Exception as e:
        logger.error(f"Failed to create department: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера.")
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/departments/{department_id}", response_class=JSONResponse)
async def get_department_details(enterprise_number: str, department_id: int, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        dept_query = "SELECT id, name, number FROM departments WHERE id = $1 AND enterprise_number = $2"
        department = await conn.fetchrow(dept_query, department_id, enterprise_number)

        if not department:
            raise HTTPException(status_code=404, detail="Отдел не найден")

        members_query = """
        SELECT
            dm.internal_phone_id as id,
            (u.first_name || ' ' || u.last_name) AS user_name,
            uip.phone_number
        FROM department_members dm
        JOIN user_internal_phones uip ON dm.internal_phone_id = uip.id
        LEFT JOIN users u ON uip.user_id = u.id
        WHERE dm.department_id = $1
        ORDER BY u.last_name, u.first_name, uip.phone_number
        """
        members = await conn.fetch(members_query, department_id)
        
        result = dict(department)
        result['members'] = [dict(member) for member in members]
        
        return JSONResponse(content=result)
    finally:
        await conn.close()

@app.put("/enterprise/{enterprise_number}/departments/{department_id}", response_class=JSONResponse)
async def update_department(
    enterprise_number: str,
    department_id: int,
    department: DepartmentUpdate,
    current_enterprise: str = Depends(get_current_enterprise)
):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        async with conn.transaction():
            # Update department details
            updated_dept = await conn.fetchrow(
                """
                UPDATE departments SET name = $1, number = $2
                WHERE id = $3 AND enterprise_number = $4
                RETURNING id, name, number
                """,
                department.name, department.number, department_id, enterprise_number
            )
            if not updated_dept:
                raise HTTPException(status_code=404, detail="Отдел не найден для обновления.")

            # Mange members
            await conn.execute("DELETE FROM department_members WHERE department_id = $1", department_id)
            
            if department.members:
                # Prepare data for copy
                records_to_insert = [(department_id, member_id) for member_id in department.members]
                await conn.copy_records_to_table(
                    'department_members',
                    records=records_to_insert,
                    columns=['department_id', 'internal_phone_id']
                )

        # После успешной транзакции вызываем перегенерацию конфига
        try:
            async with httpx.AsyncClient() as client:
                plan_service_url = f"http://localhost:8006/generate_config"
                response = await client.post(plan_service_url, json={"enterprise_id": enterprise_number}, timeout=10.0)
                response.raise_for_status()
                logger.info(f"Успешно вызван сервис plan.py для перегенерации конфига для предприятия {enterprise_number}.")
        except httpx.RequestError as e:
            logger.error(f"Не удалось вызвать сервис plan.py для перегенерации конфига: {e}")
            # Не бросаем ошибку, так как удаление уже прошло успешно

        return JSONResponse(content=dict(updated_dept))
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Отдел с таким именем или номером уже существует.")
    except asyncpg.exceptions.CheckViolationError:
        raise HTTPException(status_code=400, detail="Номер отдела должен быть в диапазоне от 901 до 999.")
    except Exception as e:
        logger.error(f"Failed to update department: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при обновлении отдела.")
    finally:
        await conn.close()

@app.delete("/enterprise/{enterprise_number}/departments/{department_id}")
async def delete_department(request: Request, enterprise_number: str, department_id: int):
    logger.info(f"Получен запрос на удаление отдела {department_id} для предприятия {enterprise_number}")
    conn = await get_db_connection()
    try:
        async with conn.transaction():
            # Сначала удаляем всех участников отдела из department_members
            await conn.execute(
                'DELETE FROM department_members WHERE department_id = $1',
                department_id
            )
            logger.info(f"Удалены участники отдела {department_id}")

            # Затем удаляем сам отдел
            result = await conn.execute(
                'DELETE FROM departments WHERE id = $1 RETURNING id',
                department_id
            )
            if not result or result.strip() == 'DELETE 0':
                raise HTTPException(status_code=404, detail="Отдел не найден")
            
            logger.info(f"Удален отдел {department_id}")

        # После успешной транзакции вызываем перегенерацию конфига
        try:
            async with httpx.AsyncClient() as client:
                plan_service_url = f"http://localhost:8006/generate_config"
                response = await client.post(plan_service_url, json={"enterprise_id": enterprise_number}, timeout=10.0)
                response.raise_for_status()
                logger.info(f"Успешно вызван сервис plan.py для перегенерации конфига для предприятия {enterprise_number}.")
        except httpx.RequestError as e:
            logger.error(f"Не удалось вызвать сервис plan.py для перегенерации конфига: {e}")
            # Не бросаем ошибку, так как удаление уже прошло успешно

        return JSONResponse(content={"message": "Отдел успешно удален"}, status_code=200)

    except HTTPException as e:
        # Логируем и перевыбрасываем, чтобы FastAPI обработал
        logger.error(f"Ошибка при удалении отдела: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при удалении отдела {department_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")
    finally:
        await conn.close()

# ——————————————————————————————————————————————————————————————————————————
# SIP Lines Management
# ——————————————————————————————————————————————————————————————————————————

@app.get("/enterprise/{enterprise_number}/sip-providers", response_class=JSONResponse)
async def get_sip_providers(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        query = "SELECT id, name FROM sip ORDER BY name"
        providers = await conn.fetch(query)
        return [dict(row) for row in providers]
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/sip-lines", response_class=JSONResponse)
async def get_sip_lines(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        SELECT
            su.id,
            su.line_name,
            su.prefix,
            p.name as provider_name,
            su.in_schema as incoming_schema_name,
            COALESCE(out_s.outgoing_schema_names, ARRAY[]::text[]) as outgoing_schema_names
        FROM
            sip_unit su
        LEFT JOIN
            sip p ON su.provider_id = p.id
        LEFT JOIN (
            SELECT
                sosa.sip_line_name,
                array_agg(sosa.schema_name) as outgoing_schema_names
            FROM
                sip_outgoing_schema_assignments sosa
            WHERE
                sosa.enterprise_number = $1
            GROUP BY
                sosa.sip_line_name
        ) out_s ON su.line_name = out_s.sip_line_name
        WHERE
            su.enterprise_number = $1
        ORDER BY
            su.id;
        """
        sip_lines = await conn.fetch(query, enterprise_number)
        logger.info(f"ПОЛУЧЕНО ИЗ БД: {len(sip_lines)} строк.")
        logger.info(f"СОДЕРЖИМОЕ: {sip_lines}")

        results = []
        for record in sip_lines:
            # Преобразуем каждую запись в словарь.
            # Поля-массивы Postgres (как `outgoing_schema_names`) asyncpg возвращает как списки Python,
            # что совместимо с JSON. Проблема могла быть в другом.
            # Но для надежности оставляем явное преобразование в dict.
            results.append(dict(record))
        return results
    except Exception as e:
        logger.error(f"Ошибка при обработке данных в get_sip_lines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/sip-lines/{line_id}", response_class=JSONResponse)
async def get_sip_line_details(enterprise_number: str, line_id: int, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = "SELECT id, line_name, password, prefix, provider_id FROM sip_unit WHERE id = $1 AND enterprise_number = $2"
        line = await conn.fetchrow(query, line_id, enterprise_number)
        if not line:
            raise HTTPException(status_code=404, detail="SIP line not found")
        return dict(line)
    finally:
        await conn.close()

@app.put("/enterprise/{enterprise_number}/sip-lines/{line_id}", response_class=JSONResponse)
async def update_sip_line(
    enterprise_number: str,
    line_id: int,
    data: SipLineUpdate,
    current_enterprise: str = Depends(get_current_enterprise)
):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        async with conn.transaction():
            shablon = await conn.fetchval("SELECT shablon FROM sip WHERE id = $1", data.provider_id)
            if not shablon:
                raise HTTPException(status_code=404, detail="Шаблон для данного SIP-провайдера не найден.")
            
            info_text = shablon.replace("LOGIN", data.line_name).replace("PASSWORD", data.password)
            
            query = """
            UPDATE sip_unit
            SET line_name = $1, password = $2, prefix = $3, info = $4, provider_id = $5
            WHERE id = $6 AND enterprise_number = $7
            RETURNING id, enterprise_number, line_name, prefix
            """
            updated_line = await conn.fetchrow(
                query,
                data.line_name, data.password, data.prefix,
                info_text, data.provider_id, line_id, enterprise_number
            )
            
            if not updated_line:
                raise HTTPException(status_code=404, detail="SIP line not found for update")

        # Регенерация SIP конфига через план-сервис (асинхронно)
        asyncio.create_task(_regenerate_sip_config_via_plan_service(enterprise_number))

        return dict(updated_line)

    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Линия с таким именем уже существует для этого предприятия.")
    except Exception as e:
        logger.error(f"Failed to update SIP line: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при обновлении SIP-линии.")
    finally:
        await conn.close()

@app.post("/enterprise/{enterprise_number}/sip-lines", response_class=JSONResponse, status_code=status.HTTP_201_CREATED)
async def create_sip_line(
    enterprise_number: str,
    data: SipLineCreate,
    current_enterprise: str = Depends(get_current_enterprise)
):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # 1. Get the template from the sip table
        shablon = await conn.fetchval("SELECT shablon FROM sip WHERE id = $1", data.provider_id)
        if not shablon:
            raise HTTPException(status_code=404, detail="Шаблон для данного SIP-провайдера не найден.")
        
        # 2. Replace placeholders
        info_text = shablon.replace("LOGIN", data.line_name).replace("PASSWORD", data.password)
        
        # 3. Insert the new record into the sip_unit table
        query = """
        INSERT INTO sip_unit (enterprise_number, line_name, password, prefix, info, provider_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, enterprise_number, line_name, prefix
        """
        new_line_record = await conn.fetchrow(
            query,
            enterprise_number,
            data.line_name,
            data.password,
            data.prefix,
            info_text,
            data.provider_id
        )

        # Регенерация SIP конфига через план-сервис (асинхронно)
        asyncio.create_task(_regenerate_sip_config_via_plan_service(enterprise_number))
        
        return dict(new_line_record)

    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Линия с таким именем уже существует для этого предприятия.")
    except Exception as e:
        logger.error(f"Failed to create SIP line: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при создании SIP-линии.")
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/internal-phones/{phone_number}/details", response_class=JSONResponse)
async def get_internal_phone_details(enterprise_number: str, phone_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = "SELECT phone_number, password FROM user_internal_phones WHERE enterprise_number = $1 AND phone_number = $2"
        phone_details = await conn.fetchrow(query, enterprise_number, phone_number)

        if not phone_details:
            raise HTTPException(status_code=404, detail="Internal phone not found")

        return JSONResponse(content=dict(phone_details))
    finally:
        await conn.close()

@app.delete("/enterprise/{enterprise_number}/internal-phones/{phone_number}", response_class=Response)
async def delete_internal_phone(enterprise_number: str, phone_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # Получаем запись о телефоне, включая его id и user_id
        phone_record = await conn.fetchrow("SELECT id, user_id FROM user_internal_phones WHERE enterprise_number = $1 AND phone_number = $2", enterprise_number, phone_number)

        if not phone_record:
            raise HTTPException(status_code=404, detail="Внутренний номер не найден.")

        phone_id = phone_record['id']
        assigned_user = phone_record['user_id']
        
        # 1. Проверить, привязан ли номер к пользователю
        if assigned_user is not None:
            raise HTTPException(status_code=409, detail=f"Невозможно удалить номер {phone_number}, так как он назначен пользователю.")

        # 2. Проверить, используется ли номер во входящих схемах, используя phone_id
        incoming_schemas = await conn.fetch(
            "SELECT ds.schema_name FROM dial_schemas ds JOIN user_internal_phone_incoming_assignments uipa ON ds.schema_id = uipa.schema_id WHERE uipa.internal_phone_id = $1 AND ds.enterprise_id = $2",
            phone_id, enterprise_number)
        
        # 3. Проверить, используется ли номер в исходящих схемах
        outgoing_schemas = await conn.fetch(
            "SELECT ds.schema_name FROM user_internal_phones uip JOIN dial_schemas ds ON uip.outgoing_schema_id = ds.schema_id WHERE uip.id = $1 AND uip.enterprise_number = $2",
            phone_id, enterprise_number)

        conflicts = []
        if incoming_schemas:
            conflicts.append(f"используется во входящих схемах: {', '.join([s['schema_name'] for s in incoming_schemas])}")
        if outgoing_schemas:
            conflicts.append(f"используется в исходящих схемах: {', '.join([s['schema_name'] for s in outgoing_schemas])}")

        if conflicts:
            error_message = f"Невозможно удалить номер {phone_number}, так как он " + " и ".join(conflicts) + "."
            raise HTTPException(status_code=409, detail=error_message)

        # Если проверки пройдены, удаляем номер по его ID
        delete_query = "DELETE FROM user_internal_phones WHERE id = $1"
        result = await conn.execute(delete_query, phone_id)

        if result.strip() == "DELETE 0":
             raise HTTPException(status_code=404, detail="Номер не найден для удаления.")

        # Регенерация SIP конфига через план-сервис (асинхронно)
        asyncio.create_task(_regenerate_sip_config_via_plan_service(enterprise_number))

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException as http_exc:
        raise http_exc # Re-raise HTTPException to be handled by FastAPI
    except Exception as e:
        logger.error(f"Error deleting internal phone {phone_number} for enterprise {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера. Не удалось удалить номер.")
    finally:
        await conn.close()

@app.post("/enterprise/{enterprise_number}/regenerate-config", status_code=status.HTTP_200_OK)
async def regenerate_config(
    enterprise_number: str,
    current_enterprise: str = Depends(get_current_enterprise)
):
    """
    Этот эндпоинт вызывает план-сервис для полной регенерации extensions.conf
    и развертывания его на удаленный Asterisk хост.
    """
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    try:
        # Вызываем план-сервис для генерации и развертывания конфига
        async with httpx.AsyncClient() as client:
            plan_service_url = f"http://localhost:8006/generate_config"
            response = await client.post(
                plan_service_url, 
                json={"enterprise_id": enterprise_number}, 
                timeout=30.0  # Увеличиваем timeout для SSH операций
            )
            response.raise_for_status()
            result = response.json()
            
            # Обрабатываем ответ от план-сервиса
            deployment_info = result.get("deployment", {})
            deployment_success = deployment_info.get("success", False)
            deployment_message = deployment_info.get("message", "Информация о развертывании недоступна")
            
            if deployment_success:
                logger.info(f"Успешная регенерация и развертывание конфига для предприятия {enterprise_number}")
                return {
                    "status": "success", 
                    "detail": "Схемы звонков обновлены"
                }
            else:
                logger.warning(f"Конфиг сгенерирован, но развертывание не удалось для предприятия {enterprise_number}: {deployment_message}")
                return {
                    "status": "warning", 
                    "detail": "Нет связи с АТС, повторите попытку позже"
                }
                
    except httpx.TimeoutException:
        logger.error(f"Timeout при вызове план-сервиса для предприятия {enterprise_number}")
        raise HTTPException(status_code=408, detail="Нет связи с АТС, повторите попытку позже")
    except httpx.RequestError as e:
        logger.error(f"Ошибка при вызове план-сервиса для предприятия {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail="Нет связи с АТС, повторите попытку позже")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при регенерации конфига для предприятия {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Нет связи с АТС, повторите попытку позже")

@app.get("/enterprise/{enterprise_number}/dial-schemas", response_class=JSONResponse)
async def get_dial_schemas(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        SELECT id, name FROM dial_schemas WHERE enterprise_id = $1
        """
        schemas = await conn.fetch(query, enterprise_number)
        return [dict(row) for row in schemas]
    finally:
        await conn.close()

@app.delete("/enterprise/{enterprise_number}/sip-lines/{line_id}", response_class=Response)
async def delete_sip_line(enterprise_number: str, line_id: int, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # 1. Получить информацию о SIP-линии, включая ее имя и входящую схему
        line_info = await conn.fetchrow("SELECT line_name, in_schema FROM sip_unit WHERE id = $1 AND enterprise_number = $2", line_id, enterprise_number)

        if not line_info:
            raise HTTPException(status_code=404, detail="SIP-линия не найдена.")

        line_name = line_info['line_name']
        incoming_schema = line_info['in_schema']

        conflicts = []

        # 2. Проверить, привязана ли линия к входящей схеме
        if incoming_schema:
            conflicts.append(f"Входящая схема: {incoming_schema}")

        # 3. Проверить, используется ли линия в исходящих схемах
        outgoing_schemas = await conn.fetch(
            "SELECT schema_name FROM sip_outgoing_schema_assignments WHERE sip_line_name = $1 AND enterprise_number = $2",
            line_name, enterprise_number
        )
        
        if outgoing_schemas:
            for s in outgoing_schemas:
                conflicts.append(f"Исходящая схема: {s['schema_name']}")
        
        # 4. Если есть конфликты, вернуть ошибку
        if conflicts:
            details = f"Невозможно удалить SIP-линию '{line_name}', так как она используется в следующих схемах: " + ", ".join(conflicts)
            raise HTTPException(status_code=409, detail=details)

        # 5. Если конфликтов нет, удалить линию
        await conn.execute("DELETE FROM sip_unit WHERE id = $1 AND enterprise_number = $2", line_id, enterprise_number)
        
        # Регенерация SIP конфига через план-сервис (асинхронно)
        asyncio.create_task(_regenerate_sip_config_via_plan_service(enterprise_number))
        
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete SIP line: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера.")
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/phone-config-data", response_class=JSONResponse)
async def get_phone_config_data(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    """Получение конфигурационных данных для настройки телефона"""
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        # Получаем IP адрес предприятия
        enterprise_query = "SELECT ip FROM enterprises WHERE number = $1"
        enterprise_row = await conn.fetchrow(enterprise_query, enterprise_number)
        
        if not enterprise_row or not enterprise_row['ip']:
            raise HTTPException(status_code=404, detail="IP адрес предприятия не найден")
        
        host_ip = enterprise_row['ip']
        
        # SSH команды для получения конфигурационных файлов
        commands = {
            'sip_conf': f"sshpass -p '5atx9Ate@pbx' ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -p 5059 root@{host_ip} 'cat /etc/asterisk/sip.conf | grep -E \"(externip|bindport)\"'",
            'interfaces': f"sshpass -p '5atx9Ate@pbx' ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -p 5059 root@{host_ip} 'cat /etc/network/interfaces | grep address'"
        }
        
        config_data = {}
        
        # Выполняем SSH команды
        for key, command in commands.items():
            try:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
                
                if process.returncode == 0:
                    config_data[key] = stdout.decode('utf-8').strip()
                else:
                    config_data[key] = ''
                    
            except asyncio.TimeoutError:
                config_data[key] = ''
            except Exception as e:
                logger.error(f"Error executing SSH command for {key}: {e}")
                config_data[key] = ''
        
        # Парсим данные из sip.conf
        sip_conf_content = config_data.get('sip_conf', '')
        externip = None
        bindport = '5060'
        has_externip = False
        
        for line in sip_conf_content.split('\n'):
            line = line.strip()
            if line.startswith('externip='):
                externip = line.split('=')[1].strip()
                has_externip = True
            elif line.startswith('bindport='):
                bindport = line.split('=')[1].strip()
        
        # Парсим локальный IP из interfaces
        interfaces_content = config_data.get('interfaces', '')
        local_ip = None
        
        for line in interfaces_content.split('\n'):
            line = line.strip()
            if 'address' in line:
                parts = line.split()
                if len(parts) >= 2:
                    local_ip = parts[1].strip()
                    break
        
        return JSONResponse(content={
            'externip': externip,
            'bindport': bindport,
            'localIp': local_ip,
            'hasExternip': has_externip
        })
        
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail=f"Таймаут при подключении к хосту {host_ip}")
    except Exception as e:
        logger.error(f"Error getting phone config data for enterprise {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка получения конфигурационных данных: {str(e)}")
    finally:
        await conn.close()

@app.get("/admin/check-internal-phones-ip/{enterprise_number}", response_class=JSONResponse)
async def check_internal_phones_ip(enterprise_number: str):
    """Получение IP адресов регистрации внутренних линий для конкретного предприятия"""
    import asyncio
    from datetime import datetime
    
    try:
        # Получаем информацию о предприятии
        conn = await get_db_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="DB connection failed")
        
        try:
            enterprise_query = "SELECT ip FROM enterprises WHERE number = $1"
            enterprise_record = await conn.fetchrow(enterprise_query, enterprise_number)
            
            if not enterprise_record:
                return JSONResponse({'success': False, 'error': 'Enterprise not found'}, status_code=404)
                
            enterprise_ip = enterprise_record['ip']
            if not enterprise_ip:
                return JSONResponse({'success': False, 'error': 'Enterprise IP not configured'}, status_code=400)
            
            # SSH команда для получения детальной информации о SIP peers
            cmd = [
                'sshpass', '-p', '5atx9Ate@pbx',
                'ssh', 
                '-o', 'ConnectTimeout=5',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'LogLevel=ERROR',
                '-p', '5059',
                f'root@{enterprise_ip}',
                'timeout 15 asterisk -rx "sip show peers"'
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
                
                if process.returncode != 0:
                    error_output = stderr.decode('utf-8', errors='ignore')
                    return JSONResponse({
                        'success': False, 
                        'error': f'SSH command failed: {error_output.strip() or "Unknown error"}'
                    }, status_code=500)
                
                # Парсим вывод команды sip show peers
                output = stdout.decode('utf-8', errors='ignore')
                lines = output.strip().split('\n')
                
                internal_phones = {}
                
                for line in lines:
                    # Пропускаем заголовки и служебные строки
                    if 'Name/username' in line or 'sip peers' in line or not line.strip():
                        continue
                    
                    # Парсим строку: "150/150         (Unspecified)    D  No         No             0    UNREACHABLE"
                    # или: "151/151         192.168.1.100    D  Yes        Yes            0    OK (1 ms)"
                    parts = line.split()
                    if len(parts) < 6:
                        continue
                    
                    name_part = parts[0]  # Например: "150/150"
                    ip_part = parts[1] if len(parts) > 1 else "(Unspecified)"
                    
                    # Извлекаем номер внутренней линии
                    peer_name = name_part.split('/')[0]
                    
                    # Проверяем, что это внутренняя линия (3-значная, кроме 301/302)
                    if len(peer_name) == 3 and peer_name.isdigit() and peer_name not in ['301', '302']:
                        # Определяем статус регистрации
                        status = 'online' if ' OK ' in line else 'offline'
                        
                        # Извлекаем IP адрес
                        if ip_part == '(Unspecified)' or ip_part == 'Unspecified':
                            ip_address = None
                        else:
                            # IP может быть в формате "192.168.1.100:5060" или просто "192.168.1.100"
                            ip_address = ip_part.split(':')[0]
                        
                        internal_phones[peer_name] = {
                            'phone_number': peer_name,
                            'ip_address': ip_address,
                            'status': status,
                            'raw_line': line.strip()
                        }
                
                logger.info(f"Found {len(internal_phones)} internal phones for enterprise {enterprise_number}")
                
                return JSONResponse({
                    'success': True,
                    'enterprise_number': enterprise_number,
                    'enterprise_ip': enterprise_ip,
                    'internal_phones': internal_phones,
                    'total_found': len(internal_phones),
                    'checked_at': datetime.now().isoformat()
                })
                
            except asyncio.TimeoutError:
                return JSONResponse({
                    'success': False, 
                    'error': 'SSH connection timeout'
                }, status_code=500)
                
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Error checking internal phones IP for enterprise {enterprise_number}: {e}", exc_info=True)
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)

@app.get("/telegram-users")
async def get_telegram_users():
    """Получить список пользователей с Telegram-авторизацией"""
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        users = await conn.fetch("""
            SELECT u.id, u.email, u.first_name, u.last_name, u.enterprise_number,
                   u.telegram_authorized, u.telegram_tg_id, u.personal_phone,
                   e.name as enterprise_name
            FROM users u
            JOIN enterprises e ON u.enterprise_number = e.number
            WHERE u.telegram_authorized = TRUE
            ORDER BY u.enterprise_number, u.last_name, u.first_name
        """)
        
        return [dict(user) for user in users]
        
    except Exception as e:
        logger.error(f"Ошибка получения Telegram пользователей: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        await conn.close()

@app.post("/revoke-telegram-auth/{user_id}")
async def revoke_telegram_auth(user_id: int):
    """Отзыв Telegram-авторизации пользователя (для админов)"""
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        # Получаем telegram_tg_id перед удалением
        user = await conn.fetchrow(
            "SELECT telegram_tg_id FROM users WHERE id = $1", user_id
        )
        
        if not user or not user['telegram_tg_id']:
            return {"success": False, "message": "Пользователь не авторизован в Telegram"}
        
        # Сбрасываем авторизацию
        await conn.execute("""
            UPDATE users 
            SET telegram_authorized = FALSE,
                telegram_tg_id = NULL,
                telegram_auth_code = NULL,
                telegram_auth_expires = NULL
            WHERE id = $1
        """, user_id)
        
        # Удаляем из telegram_users
        await conn.execute("""
            DELETE FROM telegram_users WHERE tg_id = $1
        """, user['telegram_tg_id'])
        
        return {"success": True, "message": "Telegram-авторизация отозвана"}
        
    except Exception as e:
        logger.error(f"Ошибка отзыва Telegram-авторизации: {e}")
        return {"success": False, "message": "Ошибка сервера"}
    finally:
        await conn.close()

@app.get("/services", response_class=JSONResponse)
async def get_services_status():
    """Получение статуса всех сервисов из all.sh"""
    try:
        # Определяем сервисы и их порты (из all.sh)
        services_info = {
            "admin": {"port": 8004, "script": "admin.sh", "app": "admin"},
            "dial": {"port": 8005, "script": "dial.sh", "app": "dial"},
            "111": {"port": 8000, "script": "111.sh", "app": "main"},
            "plan": {"port": 8006, "script": "plan.sh", "app": "plan"},
            "sms": {"port": 8002, "script": "sms.sh", "app": "goip_sms_service"},
            "sms_send": {"port": 8013, "script": "sms_send.sh", "app": "send_service_sms"},
            "send_user_sms": {"port": 8014, "script": "send_user_sms.sh", "app": "send_user_sms"},
            "auth": {"port": 8015, "script": "auth.sh", "app": "auth"},
            "telegram": {"port": 8016, "script": "telegram.sh", "app": "telegram_auth_service"},
            "download": {"port": 8007, "script": "download.sh", "app": "download"},
            "goip": {"port": 8008, "script": "goip.sh", "app": "goip_service"},
            "desk": {"port": 8011, "script": "desk.sh", "app": "desk"},
            "call": {"port": 8012, "script": "call.sh", "app": "call"},
            "miniapp": {"port": 8017, "script": "miniapp.sh", "app": "miniapp_service"},
            "asterisk": {"port": 8018, "script": "asterisk.sh", "app": "asterisk"},
            "integration_cache": {"port": 8020, "script": "integration_cache.sh", "app": "integration_cache"},
            "reboot": {"port": 8009, "script": "reboot.sh", "app": "reboot.py"},
            "ewelink": {"port": 8010, "script": "ewelink.sh", "app": "ewelink_api"}
        }
        
        services = []
        
        for service_name, info in services_info.items():
            try:
                # Проверяем статус сервиса
                status = "unknown"
                
                if info["port"]:
                    # Проверяем по порту - более надежный метод
                    netstat_result = subprocess.run(
                        ["netstat", "-tlnp"], 
                        capture_output=True, 
                        text=True, 
                        timeout=5
                    )
                    # Проверяем и 0.0.0.0 и 127.0.0.1 привязки
                    port_pattern = f":{info['port']}"
                    port_found = port_pattern in netstat_result.stdout
                    
                    # Дополнительная проверка по процессу для download (может не показывать порт сразу)
                    if not port_found and service_name == "download":
                        ps_result = subprocess.run(
                            ["ps", "aux"], 
                            capture_output=True, 
                            text=True, 
                            timeout=5
                        )
                        if "uvicorn download:app" in ps_result.stdout:
                            status = "running"
                        else:
                            status = "stopped" 
                    else:
                        status = "running" if port_found else "stopped"
                else:
                    # Для сервисов без порта проверяем по процессу
                    ps_result = subprocess.run(
                        ["ps", "aux"], 
                        capture_output=True, 
                        text=True, 
                        timeout=5
                    )
                    # Используем более гибкий поиск процесса
                    if service_name == "reboot" and "reboot.py" in ps_result.stdout:
                        status = "running"
                    elif service_name == "ewelink" and "ewelink_api" in ps_result.stdout:
                        status = "running"
                    elif info["app"] in ps_result.stdout:
                        status = "running"
                    else:
                        status = "stopped"
                
                services.append({
                    "name": service_name,
                    "script": info["script"],
                    "port": info["port"],
                    "app": info["app"],
                    "status": status
                })
                
            except subprocess.TimeoutExpired:
                services.append({
                    "name": service_name,
                    "script": info["script"],
                    "port": info["port"],
                    "app": info["app"],
                    "status": "timeout"
                })
            except Exception as e:
                logger.error(f"Ошибка проверки сервиса {service_name}: {e}")
                services.append({
                    "name": service_name,
                    "script": info["script"],
                    "port": info["port"],
                    "app": info["app"],
                    "status": "error"
                })
        
        return {"success": True, "services": services}
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса сервисов: {e}")
        return {"success": False, "message": "Ошибка сервера"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004) 