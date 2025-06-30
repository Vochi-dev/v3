# -*- coding: utf-8 -*-
import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query, status, Body, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncpg
import logging
from typing import Dict, Optional, List
import jwt
from datetime import datetime
import random
import string
from pydantic import BaseModel
import time
import os
import uuid
import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import shutil
import httpx

from app.config import JWT_SECRET_KEY, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT

# A set of reserved numbers that cannot be assigned.
RESERVED_INTERNAL_NUMBERS = {301, 302, 555}

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
# Enterprise Admin Dashboard & API
# ——————————————————————————————————————————————————————————————————————————

@app.get("/enterprise/{enterprise_number}/dashboard", response_class=HTMLResponse)
async def enterprise_dashboard(request: Request, enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещен")
    enterprise = await get_enterprise_by_number_from_db(enterprise_number)
    if not enterprise:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предприятие не найдено")
    return templates.TemplateResponse("enterprise_admin/dashboard.html", {"request": request, "enterprise": enterprise})

@app.get("/enterprise/{enterprise_number}/users", response_class=JSONResponse)
async def get_enterprise_users(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
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
                    NULL AS outgoing_schema_name
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
                    ds.schema_name AS outgoing_schema_name
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
                    "lines": []
                }
            
            users_data[user_id]['lines'].append({
                "phone_number": record['phone_number'],
                "line_type": record['line_type'],
                "incoming_schema_names": record['incoming_schema_names'] or [],
                "outgoing_schema_name": record['outgoing_schema_name']
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
                INSERT INTO users (enterprise_number, email, first_name, last_name, patronymic, personal_phone, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'active')
                RETURNING id
                """,
                enterprise_number, user_data.email, user_data.first_name, user_data.last_name,
                user_data.patronymic, user_data.personal_phone
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
            SELECT id, email, first_name, last_name, patronymic, personal_phone
            FROM users
            WHERE id = $1 AND enterprise_number = $2;
        """
        user_record = await conn.fetchrow(user_query, user_id, enterprise_number)

        if not user_record:
            raise HTTPException(status_code=404, detail="User not found")

        user_details = dict(user_record)

        internal_phones_query = """
            SELECT phone_number 
            FROM user_internal_phones 
            WHERE user_id = $1 AND enterprise_number = $2;
        """
        internal_phones_records = await conn.fetch(internal_phones_query, user_id, enterprise_number)
        
        user_details['internal_phones'] = [record['phone_number'] for record in internal_phones_records]

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

        await conn.execute(
            "UPDATE users SET email = $1, first_name = $2, last_name = $3, patronymic = $4, personal_phone = $5 WHERE id = $6 AND enterprise_number = $7",
            user_data.email, user_data.first_name, user_data.last_name, user_data.patronymic, user_data.personal_phone, user_id, enterprise_number
        )
        if user_data.internal_phones is not None:
            async with conn.transaction():
                await conn.execute("UPDATE user_internal_phones SET user_id = NULL WHERE user_id = $1 AND enterprise_number = $2", user_id, enterprise_number)
                if user_data.internal_phones:
                    await conn.execute("UPDATE user_internal_phones SET user_id = $1 WHERE enterprise_number = $2 AND phone_number = ANY($3::text[])",
                                       user_id, enterprise_number, user_data.internal_phones)
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

        # Логика создания файла
        try:
            config_dir = Path("music") / enterprise_number
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file_path = config_dir / "sip_addproviders.conf"
            
            # Генерируем полное содержимое файла
            full_config_content = await _generate_sip_addproviders_conf(conn, enterprise_number)
            
            with open(config_file_path, "w") as f:
                f.write(full_config_content)
            logger.info(f"Конфигурационный файл '{config_file_path}' успешно создан/обновлен для внутреннего номера.")

        except Exception as e:
            logger.error(f"Не удалось создать/обновить конфигурационный файл: {e}", exc_info=True)


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
        query = """
        SELECT
            g.gateway_name,
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
                '{}'::text[]
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
                    'lines': []
                }
            if row['id'] is not None:
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
                    'redirect': row['redirect']
                }
                gateways[gateway_name]['lines'].append(line)
        return JSONResponse(content=list(gateways.values()))
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
        return dict(row)
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
        row = await conn.fetchrow(
            query,
            data.get("line_name"),
            data.get("phone_number"),
            data.get("prefix"),
            line_id,
            enterprise_number
        )
        if not row:
            raise HTTPException(status_code=404, detail="Линия не найдена")
        return dict(row)
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
        SELECT id, display_name, file_type, file_path, original_filename, created_at
        FROM music_files
        WHERE enterprise_number = $1
        ORDER BY created_at DESC
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
                "created_at": file["created_at"].isoformat()
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
            # Не бросаем HTTPException, чтобы не откатывать уже сохраненные данные.
            # Пользователь получит успешный ответ, но в логах будет ошибка.

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

        # После успешного обновления в базе, создаем/обновляем файл
        try:
            config_dir = Path(f"music/{enterprise_number}")
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "sip_addproviders.conf"
            
            # Генерируем полное содержимое файла
            full_config_content = await _generate_sip_addproviders_conf(conn, enterprise_number)
            
            with open(config_path, "w") as f:
                f.write(full_config_content)
            logging.info(f"Конфигурационный файл '{config_path}' успешно создан/обновлен для SIP-линии.")

        except Exception as e:
            logging.error(f"Не удалось создать файл sip_addproviders.conf для предприятия {enterprise_number}: {e}")
            # Пока просто логируем
        
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

        # После успешного создания в базе, создаем/обновляем файл
        try:
            config_dir = Path(f"music/{enterprise_number}")
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "sip_addproviders.conf"
            
            # Генерируем полное содержимое файла
            full_config_content = await _generate_sip_addproviders_conf(conn, enterprise_number)
            
            with open(config_path, "w") as f:
                f.write(full_config_content)
            logging.info(f"Конфигурационный файл '{config_path}' успешно создан/обновлен для SIP-линии.")

        except Exception as e:
            logging.error(f"Не удалось создать файл sip_addproviders.conf для предприятия {enterprise_number}: {e}")
            # Пока просто логируем
        
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
            "SELECT osa.schema_name FROM gsm_outgoing_schema_assignments osa WHERE osa.gsm_line_id = $1 AND osa.enterprise_number = $2",
            phone_number, enterprise_number)

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

        # Логика создания файла после удаления
        try:
            config_dir = Path(f"music/{enterprise_number}")
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "sip_addproviders.conf"
            
            # Генерируем полное содержимое файла
            full_config_content = await _generate_sip_addproviders_conf(conn, enterprise_number)

            with open(config_path, "w") as f:
                f.write(full_config_content)
            logger.info(f"Конфигурационный файл '{config_path}' успешно обновлен после удаления внутреннего номера.")
        except Exception as e:
            logger.error(f"Не удалось обновить конфигурационный файл после удаления внутреннего номера: {e}")
            # Не прерываем процесс из-за ошибки записи файла, но логируем ее

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
    Этот эндпоинт принудительно перезаписывает конфигурационный файл.
    Он не вносит никаких изменений в базу данных.
    """
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        config_dir = Path(f"music/{enterprise_number}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "sip_addproviders.conf"
        
        # Генерируем полное содержимое файла
        full_config_content = await _generate_sip_addproviders_conf(conn, enterprise_number)

        with open(config_path, "w") as f:
            f.write(full_config_content)
            
        logging.info(f"Файл sip_addproviders.conf для предприятия {enterprise_number} был успешно пересоздан по запросу.")
        return {"status": "success", "detail": "Config file regenerated."}
    except Exception as e:
        logging.error(f"Не удалось пересоздать файл sip_addproviders.conf для предприятия {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось обновить конфигурационный файл.")
    finally:
        await conn.close()

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
        
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete SIP line: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера.")
    finally:
        await conn.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004) 