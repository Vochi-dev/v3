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

async def get_db_connection():
    try:
        conn = await asyncpg.connect(user=POSTGRES_USER, password=POSTGRES_PASSWORD, database=POSTGRES_DB, host=POSTGRES_HOST, port=POSTGRES_PORT)
        return conn
    except asyncpg.PostgresError as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

async def get_enterprise_by_number_from_db(number: str) -> Optional[Dict]:
    conn = await get_db_connection()
    if conn:
        try:
            row = await conn.fetchrow("SELECT number, name FROM enterprises WHERE number = $1", number)
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
        query = """
        WITH user_phones AS (
            SELECT user_id, array_agg(phone_number ORDER BY (CASE WHEN phone_number ~ '^[0-9]+$' THEN phone_number::int END)) as internal_phones
            FROM user_internal_phones WHERE enterprise_number = $1 GROUP BY user_id
        )
        SELECT
            u.id, u.first_name, u.last_name, u.patronymic, (u.first_name || ' ' || u.last_name) AS full_name,
            u.email, u.personal_phone, COALESCE(up.internal_phones, ARRAY[]::text[]) as internal_phones,
            '' AS ip_address, '' AS in_schema, '' AS out_schema, '' AS roles, '' AS departments, '' AS f_me
        FROM users u
        LEFT JOIN user_phones up ON u.id = up.user_id
        WHERE u.enterprise_number = $1 ORDER BY u.last_name, u.first_name
        """
        user_rows = await conn.fetch(query, enterprise_number)
        
        unassigned_phones_query = """
        SELECT array_agg(phone_number ORDER BY (CASE WHEN phone_number ~ '^[0-9]+$' THEN phone_number::int END)) as phones
        FROM user_internal_phones WHERE enterprise_number = $1 AND user_id IS NULL
        """
        unassigned_row = await conn.fetchrow(unassigned_phones_query, enterprise_number)

        results = {
            "users": [dict(row) for row in user_rows],
            "unassigned_phones": unassigned_row['phones'] if unassigned_row and unassigned_row['phones'] else []
        }
        return JSONResponse(content=results)
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/internal-phones/all", response_class=JSONResponse)
async def get_all_internal_phones(enterprise_number: str, current_enterprise: str = Depends(get_current_enterprise)):
    if enterprise_number != current_enterprise:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")

    try:
        query = """
        SELECT p.phone_number, p.user_id, (u.first_name || ' ' || u.last_name) AS manager_name
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещен")

    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="Не удалось подключиться к базе данных")
    
    try:
        async with conn.transaction():
            await conn.execute("UPDATE user_internal_phones SET user_id = NULL WHERE user_id = $1 AND enterprise_number = $2", user_id, enterprise_number)
            result = await conn.execute("DELETE FROM users WHERE id = $1 AND enterprise_number = $2", user_id, enterprise_number)
            if result == 'DELETE 0':
                raise HTTPException(status_code=404, detail="Пользователь не найден")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
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
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        await conn.execute("INSERT INTO user_internal_phones (user_id, phone_number, password, enterprise_number) VALUES (NULL, $1, $2, $3)",
                           data.phone_number, data.password, enterprise_number)
        return JSONResponse(content={"status": "success"})
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Этот внутренний номер уже занят.")
    finally:
        await conn.close()

@app.get("/enterprise/{enterprise_number}/gsm-lines/all", response_class=JSONResponse)
async def get_enterprise_gsm_lines(enterprise_number: str):
    conn = await get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="DB connection failed")
    try:
        query = """
        SELECT g.gateway_name, g.id as gateway_id,
               gl.id, gl.line_id, gl.internal_id, gl.prefix, gl.phone_number,
               gl.line_name, gl.in_schema, gl.out_schema, gl.shop, gl.slot, gl.redirect
        FROM goip g
        LEFT JOIN gsm_lines gl ON g.id = gl.goip_id
        WHERE g.enterprise_number = $1
        ORDER BY g.gateway_name, gl.id
        """
        rows = await conn.fetch(query, enterprise_number)
        gateways = {}
        for row in rows:
            gateway_name = row['gateway_name']
            if gateway_name not in gateways:
                gateways[gateway_name] = {
                    'gateway_name': gateway_name,
                    'gateway_id': row['gateway_id'],
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
                    'in_schema': row['in_schema'],
                    'out_schema': row['out_schema'],
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
        providers = await conn.fetch("SELECT id, name FROM sip ORDER BY name")
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
            s.name as provider_name
        FROM sip_unit su
        JOIN sip s ON su.provider_id = s.id
        WHERE su.enterprise_number = $1
        ORDER BY su.id
        """
        lines = await conn.fetch(query, enterprise_number)
        return [dict(row) for row in lines]
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
        
        return dict(new_line_record)

    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Линия с таким именем уже существует для этого предприятия.")
    except Exception as e:
        logger.error(f"Failed to create SIP line: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при создании SIP-линии.")
    finally:
        await conn.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004) 