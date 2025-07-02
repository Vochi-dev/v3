import os
import json
import uuid
import psycopg2
import logging
import requests
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from dotenv import load_dotenv
from datetime import datetime
from psycopg2.extras import DictCursor, execute_values
from fastapi.templating import Jinja2Templates

load_dotenv()

# --- Logger Setup ---
LOG_FILE_PATH = 'dial_service.log'
# Устанавливаем базовый уровень логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Создаем логгер для нашего приложения
logger = logging.getLogger(__name__)

# Обработчик для записи в файл с ротацией
# 1 МБ на файл, храним 5 файлов
handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=1_000_000, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s [in %(pathname)s:%(lineno)d]')
handler.setFormatter(formatter)
logger.addHandler(handler)
# --- End Logger Setup ---

app = FastAPI()

# --- Middleware for Logging Requests ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Request started: {request.method} {request.url} from {request.client.host}")
    response = await call_next(request)
    logger.info(f"Request finished: {request.method} {request.url} with status {response.status_code}")
    return response
# --- End Middleware ---

# --- DB Config ---
# Конфигурация подключения, как в app/services/postgres.py
POSTGRES_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

@contextmanager
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=POSTGRES_CONFIG['host'],
            port=POSTGRES_CONFIG['port'],
            user=POSTGRES_CONFIG['user'],
            password=POSTGRES_CONFIG['password'],
            dbname=POSTGRES_CONFIG['database']
        )
        yield conn
    finally:
        if 'conn' in locals() and conn:
            conn.close()

# --- Pydantic Models ---
class SchemaDataModel(BaseModel):
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    viewport: Dict[str, float]

class SchemaModel(BaseModel):
    schema_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    enterprise_id: str
    schema_name: str
    schema_data: SchemaDataModel
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    schema_type: str = 'incoming'

class SchemaCreateModel(BaseModel):
    schema_name: str
    schema_data: SchemaDataModel
    schema_type: str

class SchemaUpdateModel(BaseModel):
    schema_name: str
    schema_data: SchemaDataModel
    schema_type: str

class MusicFile(BaseModel):
    id: int
    display_name: str

class MobileTemplate(BaseModel):
    id: int
    name: str
    shablon: str

# --- NEW: API for Lines ---
@app.get("/api/enterprises/{enterprise_number}/lines")
async def get_lines_for_enterprise(enterprise_number: str):
    """
    Fetches a combined list of GSM and SIP lines for a given enterprise from the PostgreSQL DB.
    """
    logger.info(f"Fetching lines for enterprise_number: {enterprise_number}")
    lines = []
    
    # SQL to fetch GSM lines
    gsm_sql = "SELECT line_id, phone_number, line_name, in_schema FROM gsm_lines WHERE enterprise_number = %s ORDER BY CAST(line_id AS INTEGER) ASC;"
    
    # SQL to fetch SIP lines with provider name
    # Важно: Добавляем проверку на существование колонки in_schema
    sip_sql = """
        SELECT su.line_name as sip_id, su.line_name, s.name as provider_name,
               (CASE WHEN (SELECT true FROM information_schema.columns WHERE table_name='sip_unit' AND column_name='in_schema')
                     THEN su.in_schema
                     ELSE NULL
                END) as in_schema
        FROM sip_unit su
        JOIN sip s ON su.provider_id = s.id
        WHERE su.enterprise_number = %s
        ORDER BY su.id ASC;
    """
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Fetch GSM lines
                logger.debug(f"Executing GSM query for enterprise {enterprise_number}")
                cur.execute(gsm_sql, (enterprise_number,))
                gsm_results = cur.fetchall()
                logger.info(f"Found {len(gsm_results)} GSM lines for enterprise {enterprise_number}")
                for row in gsm_results:
                    lines.append({
                        "id": f"gsm_{row[0]}",
                        "display_name": f"{row[1] or ''} - {row[2] or ''}".strip(" -"),
                        "in_schema": row[3]
                    })
                
                # Fetch SIP lines
                logger.debug(f"Executing SIP query for enterprise {enterprise_number}")
                cur.execute(sip_sql, (enterprise_number,))
                sip_results = cur.fetchall()
                logger.info(f"Found {len(sip_results)} SIP lines for enterprise {enterprise_number}")
                for row in sip_results:
                    lines.append({
                        "id": f"sip_{row[0]}",
                        "display_name": f"{row[1] or ''} {row[2] or ''}".strip(),
                        "in_schema": row[3]
                    })
    except psycopg2.Error as e:
        logger.error(f"Database error for enterprise {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    logger.info(f"Returning a total of {len(lines)} lines for enterprise {enterprise_number}")
    return JSONResponse(content=lines)

@app.get("/api/enterprises/{enterprise_number}/users")
async def get_enterprise_users(enterprise_number: str):
    """
    Fetches a list of users for a given enterprise, along with their associated phones.
    """
    logger.info(f"Fetching users for enterprise_number: {enterprise_number}")
    
    query = """
    WITH user_phones AS (
        SELECT 
            user_id, 
            array_agg(phone_number) as internal_phones
        FROM user_internal_phones 
        WHERE enterprise_number = %s 
        GROUP BY user_id
    )
    SELECT
        u.id, 
        (u.first_name || ' ' || u.last_name) AS full_name,
        u.personal_phone, 
        COALESCE(up.internal_phones, ARRAY[]::text[]) as internal_phones
    FROM users u
    LEFT JOIN user_phones up ON u.id = up.user_id
    WHERE u.enterprise_number = %s 
    ORDER BY u.last_name, u.first_name;
    """
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (enterprise_number, enterprise_number))
                rows = cur.fetchall()
                
                # Получаем имена колонок из курсора
                colnames = [desc[0] for desc in cur.description]
                
                # Преобразуем результат в список словарей
                users = [dict(zip(colnames, row)) for row in rows]
                
                logger.info(f"Found {len(users)} users for enterprise {enterprise_number}")
                return JSONResponse(content=users)
    except psycopg2.Error as e:
        logger.error(f"Database error while fetching users for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.get("/api/enterprises/{enterprise_number}/internal_users_and_phones")
async def get_internal_users_and_phones(enterprise_number: str):
    """
    Возвращает список пользователей с их внутренними номерами, 
    а также внутренние номера, не привязанные к пользователям.
    """
    logger.info(f"Fetching internal users and phones for enterprise_number: {enterprise_number}")
    
    query = """
    SELECT
        p.phone_number,
        u.id as user_id,
        (u.first_name || ' ' || u.last_name) AS full_name,
        TRUE as is_internal
    FROM user_internal_phones p
    LEFT JOIN users u ON p.user_id = u.id
    WHERE p.enterprise_number = %s
    ORDER BY p.phone_number::int;
    """
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (enterprise_number,))
                results = cur.fetchall()
                
        logger.info(f"Found {len(results)} internal phones for enterprise {enterprise_number}")
        return JSONResponse(content=results)
    except psycopg2.Error as e:
        logger.error(f"Database error while fetching internal phones for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.get("/api/enterprises/{enterprise_number}/all_numbers_with_users")
async def get_all_numbers_with_users(enterprise_number: str):
    """
    Агрегирует всех пользователей и все номера (внутренние, личные) для предприятия.
    Добавлена информация о принадлежности внутреннего номера к исходящей схеме.
    """
    logger.info(f"Fetching all numbers and users for enterprise_number: {enterprise_number}")
    
    # Запрос для пользователей и их внутренних номеров с информацией о схеме
    query = """
    SELECT 
        u.id as user_id, 
        (u.first_name || ' ' || u.last_name) AS full_name,
        p.phone_number,
        ds.schema_name as outgoing_schema,
        'internal' as type,
        true as is_internal
    FROM user_internal_phones p
    LEFT JOIN users u ON p.user_id = u.id AND p.enterprise_number = u.enterprise_number
    LEFT JOIN dial_schemas ds ON p.outgoing_schema_id = ds.schema_id
    WHERE p.enterprise_number = %s

    UNION ALL

    SELECT 
        u.id as user_id,
        (u.first_name || ' ' || u.last_name) AS full_name,
        u.personal_phone as phone_number,
        NULL as outgoing_schema,
        'personal' as type,
        false as is_internal
    FROM users u
    WHERE u.enterprise_number = %s AND u.personal_phone IS NOT NULL;
    """
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (enterprise_number, enterprise_number))
                results = [dict(row) for row in cur.fetchall()]
        
        logger.info(f"Found {len(results)} total numbers for enterprise {enterprise_number}")
        return JSONResponse(content=results)
    except psycopg2.Error as e:
        logger.error(f"Database error while fetching all numbers for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.put("/api/enterprises/{enterprise_number}/schemas/{schema_id}/assign_lines")
async def assign_lines_to_schema(enterprise_number: str, schema_id: str, line_ids: List[str] = Body(...)):
    """
    Assigns a list of lines to a schema, clearing old assignments for THIS schema.
    It will overwrite assignments from other schemas.
    """
    logger.info(f"Assigning lines for schema {schema_id} for enterprise {enterprise_number}")
    get_name_query = "SELECT schema_name FROM dial_schemas WHERE schema_id = %s AND enterprise_id = %s;"
    schema_name = None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(get_name_query, (schema_id, enterprise_number))
                result = cur.fetchone()
                if result:
                    schema_name = result[0]
    except psycopg2.Error as e:
        logger.error(f"DB error getting schema name for {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при получении имени схемы.")

    if not schema_name:
        raise HTTPException(status_code=404, detail="Schema not found")

    gsm_ids_to_set = [line.replace('gsm_', '') for line in line_ids if line.startswith('gsm_')]
    sip_ids_to_set = [line.replace('sip_', '') for line in line_ids if line.startswith('sip_')]
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Транзакция, чтобы все выполнилось или ничего
                # 1. Снимаем привязку с линий, которые ранее принадлежали НАШЕЙ схеме, но теперь не выбраны
                cur.execute(
                    "UPDATE gsm_lines SET in_schema = NULL WHERE enterprise_number = %s AND in_schema = %s",
                    (enterprise_number, schema_name)
                )
                cur.execute(
                    "UPDATE sip_unit SET in_schema = NULL WHERE enterprise_number = %s AND in_schema = %s",
                    (enterprise_number, schema_name)
                )
                
                # 2. Устанавливаем привязку для выбранных линий (это перезапишет любую другую схему)
                if gsm_ids_to_set:
                    cur.execute(
                        "UPDATE gsm_lines SET in_schema = %s WHERE enterprise_number = %s AND line_id = ANY(%s::varchar[])",
                        (schema_name, enterprise_number, gsm_ids_to_set)
                    )
                
                if sip_ids_to_set:
                     cur.execute(
                        "UPDATE sip_unit SET in_schema = %s WHERE enterprise_number = %s AND line_name = ANY(%s::varchar[])",
                        (schema_name, enterprise_number, sip_ids_to_set)
                    )
                
                conn.commit()
    except psycopg2.Error as e:
        # Это сработает, если колонки in_schema нет в sip_unit
        if 'column "in_schema" of relation "sip_unit" does not exist' in str(e):
             logger.error("Database migration needed for 'sip_unit'.", exc_info=True)
             raise HTTPException(status_code=500, detail="Database migration needed: Column 'in_schema' does not exist in 'sip_unit'. Please run the migration script.")
        logger.error(f"Database error assigning lines for schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    return {"status": "ok"}

@app.put("/api/enterprises/{enterprise_number}/schemas/{schema_id}/assign_phones")
async def assign_phones_to_schema(enterprise_number: str, schema_id: str, phone_ids: List[str] = Body(...)):
    """
    Привязывает список внутренних номеров (телефонов) к исходящей схеме и отвязывает все остальные.
    """
    logger.info(f"Assigning phones {phone_ids} to schema {schema_id} for enterprise {enterprise_number}")

    if not schema_id or not enterprise_number:
        raise HTTPException(status_code=400, detail="Schema ID and Enterprise Number are required.")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Шаг 1: Отвязываем ВСЕ телефоны от ДАННОЙ схемы для ДАННОГО предприятия
                sql_unbind = "UPDATE user_internal_phones SET outgoing_schema_id = NULL WHERE enterprise_number = %s AND outgoing_schema_id = %s;"
                cur.execute(sql_unbind, (enterprise_number, schema_id))
                logger.info(f"Unbound all phones from schema {schema_id} for enterprise {enterprise_number}. {cur.rowcount} rows affected.")

                # Шаг 2: Привязываем выбранные телефоны к схеме
                if phone_ids:
                    # Убедимся, что все ID - строки, на случай если придут числа
                    safe_phone_ids = [str(p) for p in phone_ids]
                    
                    sql_bind = "UPDATE user_internal_phones SET outgoing_schema_id = %s WHERE enterprise_number = %s AND phone_number = ANY(%s::text[]);"
                    cur.execute(sql_bind, (schema_id, enterprise_number, safe_phone_ids))
                    logger.info(f"Bound phones {safe_phone_ids} to schema {schema_id}. {cur.rowcount} rows affected.")
                
                conn.commit()

    except psycopg2.Error as e:
        logger.error(f"Database error while assigning phones: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    
    return JSONResponse(content={"message": "Phones assigned successfully"})

# --- REFACTORED: API for Schemas ---
@app.get("/api/enterprises/{enterprise_number}/schemas", response_model=List[SchemaModel])
async def get_schemas_list(enterprise_number: str):
    """
    Fetches a list of schemas for a given enterprise from the DB.
    For outgoing schemas, it dynamically injects the most current phone assignments
    directly from the user_internal_phones table, ensuring data consistency.
    """
    query = "SELECT schema_id, enterprise_id, schema_name, created_at, schema_data, schema_type FROM dial_schemas WHERE enterprise_id = %s"
    
    # Запрос для получения актуальных привязок для ОДНОЙ схемы
    phones_query = """
        SELECT
            p.phone_number,
            (u.first_name || ' ' || u.last_name) AS full_name
        FROM user_internal_phones p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.enterprise_number = %s AND p.outgoing_schema_id = %s
        ORDER BY p.phone_number::int;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (enterprise_number,))
                results = cur.fetchall()

                processed_results = []
                for row in results:
                    row_dict = dict(row)
                    
                    # Если это исходящая схема, получаем актуальные данные
                    if row_dict.get('schema_type') == 'outgoing':
                        cur.execute(phones_query, (enterprise_number, row_dict['schema_id']))
                        assigned_phones = [dict(p) for p in cur.fetchall()]
                        
                        # Находим узел и перезаписываем данные
                        schema_data = row_dict['schema_data']
                        if schema_data and 'nodes' in schema_data:
                            for node in schema_data['nodes']:
                                # Ищем узел исходящего звонка по типу
                                if node.get('type') == 'outgoing-call':
                                    # Гарантируем, что data существует
                                    if 'data' not in node:
                                        node['data'] = {}
                                    # Перезаписываем details свежими данными из БД
                                    node['data']['phones_details'] = assigned_phones
                                    break # Нашли и обновили, выходим из цикла по узлам

                    if 'created_at' in row_dict and isinstance(row_dict['created_at'], datetime):
                        row_dict['created_at'] = row_dict['created_at'].isoformat()
                    
                    processed_results.append(row_dict)

                return JSONResponse(content=processed_results)
    except psycopg2.Error as e:
        logger.error(f"DB error fetching schemas for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.get("/api/enterprises/{enterprise_number}/schemas/{schema_id}", response_model=SchemaModel)
async def get_schema_by_id(enterprise_number: str, schema_id: str):
    logger.info(f"Fetching schema from DB by id: {schema_id} for enterprise {enterprise_number}")
    query = "SELECT * FROM dial_schemas WHERE schema_id = %s AND enterprise_id = %s;"

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (schema_id, enterprise_number))
                schema_data = cur.fetchone()
        
        if not schema_data:
            logger.warning(f"Schema not found in DB: id {schema_id}")
            raise HTTPException(status_code=404, detail="Schema not found")

        schema_dict = dict(schema_data)
        if 'created_at' in schema_dict and hasattr(schema_dict['created_at'], 'isoformat'):
            schema_dict['created_at'] = schema_dict['created_at'].isoformat()
        
        logger.info(f"Successfully fetched schema from DB: id {schema_id}")
        return SchemaModel(**schema_dict)
        
    except psycopg2.Error as e:
        logger.error(f"DB error fetching schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while fetching schema.")

@app.post("/api/enterprises/{enterprise_number}/schemas", response_model=SchemaModel)
async def create_schema(enterprise_number: str, schema_in: SchemaCreateModel):
    new_schema = SchemaModel(
        enterprise_id=enterprise_number,
        schema_name=schema_in.schema_name,
        schema_data=schema_in.schema_data,
        schema_type=schema_in.schema_type
    )
    
    schema_data_json = json.dumps(new_schema.schema_data.dict())

    query = """
        INSERT INTO dial_schemas (schema_id, enterprise_id, schema_name, schema_data, created_at, schema_type)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *;
    """
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (
                    new_schema.schema_id,
                    new_schema.enterprise_id,
                    new_schema.schema_name,
                    schema_data_json,
                    datetime.fromisoformat(new_schema.created_at),
                    new_schema.schema_type
                ))
                created_record = cur.fetchone()
                conn.commit()
        
        if not created_record:
            raise HTTPException(status_code=500, detail="Failed to create schema in DB.")

        logger.info(f"Schema created in DB with id: {new_schema.schema_id} and type: {new_schema.schema_type}")
        
        response_schema = dict(created_record)
        response_schema['created_at'] = response_schema['created_at'].isoformat()
        return SchemaModel(**response_schema)

    except psycopg2.Error as e:
        logger.error(f"DB error creating schema for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while creating schema.")

@app.put("/api/enterprises/{enterprise_number}/schemas/{schema_id}", response_model=SchemaModel)
async def update_schema(enterprise_number: str, schema_id: str, schema_update: SchemaUpdateModel):
    """
    Обновляет существующую схему и обрабатывает логику привязки.
    - Для исходящих схем: привязывает внутренние телефоны.
    - Для входящих схем: привязывает SIP-линии к полю in_schema.
    - Для исходящих схем: привязывает SIP-линии через связующую таблицу.
    """
    logger.info(f"Updating schema {schema_id} for enterprise {enterprise_number}")
    
    current_schema_name = schema_update.schema_name
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                
                # --- Логика для исходящих схем (ВНУТРЕННИЕ ТЕЛЕФОНЫ) ---
                if schema_update.schema_type == 'outgoing':
                    outgoing_node = next((node for node in schema_update.schema_data.nodes if node.get('id') == 'start-outgoing'), None)
                    if outgoing_node:
                        # Телефоны, которым разрешено использовать эту схему
                        phones_to_assign = set(outgoing_node.get('data', {}).get('phones', []))
                        
                        # Сначала отвязываем все телефоны от этой схемы
                        cur.execute(
                            "UPDATE user_internal_phones SET outgoing_schema_id = NULL WHERE enterprise_number = %s AND outgoing_schema_id = %s",
                            (enterprise_number, schema_id)
                        )
                        logger.info(f"Unbound all phones from schema {schema_id} for enterprise {enterprise_number}. {cur.rowcount} rows affected.")
                        
                        # Затем привязываем новые
                        if phones_to_assign:
                            cur.execute(
                                "UPDATE user_internal_phones SET outgoing_schema_id = %s WHERE enterprise_number = %s AND phone_number = ANY(%s::varchar[])",
                                (schema_id, enterprise_number, list(phones_to_assign))
                            )
                            logger.info(f"Bound phones {list(phones_to_assign)} to schema {schema_id}. {cur.rowcount} rows affected.")

                # --- ОБЩАЯ ЛОГИКА для SIP-линий (ВХОДЯЩИЕ И ИСХОДЯЩИЕ) ---
                sip_lines_in_schema = {
                    str(line.get('line_id')).replace('sip_', '')
                    for node in schema_update.schema_data.nodes if node.get('type') == 'externalLines'
                    for line in node.get('data', {}).get('external_lines', []) if str(line.get('line_id')).startswith('sip_')
                }

                # --- НОВАЯ ЛОГИКА: GSM-линии ---
                gsm_lines_in_schema = {
                    str(line.get('line_id')).replace('gsm_', '')
                    for node in schema_update.schema_data.nodes if node.get('type') == 'externalLines'
                    for line in node.get('data', {}).get('external_lines', []) if str(line.get('line_id')).startswith('gsm_')
                }

                # --- Логика для ВХОДЯЩИХ схем ---
                if schema_update.schema_type == 'incoming':
                    logger.info(f"Updating INCOMING schema SIP assignments for '{current_schema_name}'")
                    cur.execute(
                        "UPDATE sip_unit SET in_schema = NULL WHERE enterprise_number = %s AND in_schema = %s",
                        (enterprise_number, current_schema_name)
                    )
                    if sip_lines_in_schema:
                        cur.execute(
                            "UPDATE sip_unit SET in_schema = %s WHERE enterprise_number = %s AND line_name = ANY(%s::varchar[])",
                            (current_schema_name, enterprise_number, list(sip_lines_in_schema))
                        )
                
                # --- Логика для ИСХОДЯЩИХ схем ---
                elif schema_update.schema_type == 'outgoing':
                    # --- Обработка SIP-линий ---
                    logger.info(f"Updating OUTGOING schema SIP assignments for '{current_schema_name}'")
                    cur.execute(
                        "DELETE FROM sip_outgoing_schema_assignments WHERE enterprise_number = %s AND schema_name = %s",
                        (enterprise_number, current_schema_name)
                    )
                    if sip_lines_in_schema:
                        logger.info(f"Assigning SIP lines {sip_lines_in_schema} to outgoing schema '{current_schema_name}'")
                        assignment_data = [
                            (enterprise_number, line_name, current_schema_name)
                            for line_name in sip_lines_in_schema
                        ]
                        cur.executemany(
                            "INSERT INTO sip_outgoing_schema_assignments (enterprise_number, sip_line_name, schema_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            assignment_data
                        )
                    
                    # --- НОВАЯ ЛОГИКА: Обработка GSM-линий для исходящих схем ---
                    logger.info(f"Updating OUTGOING schema GSM assignments for '{current_schema_name}'")
                    # 1. Отвязываем схему от всех GSM-линий, где она могла быть раньше
                    cur.execute(
                        "DELETE FROM gsm_outgoing_schema_assignments WHERE enterprise_number = %s AND schema_name = %s",
                        (current_schema_name, enterprise_number)
                    )
                    # 2. Привязываем схему ко всем нужным GSM-линиям
                    if gsm_lines_in_schema:
                        logger.info(f"Assigning GSM lines {gsm_lines_in_schema} to outgoing schema '{current_schema_name}'")
                        assignment_data = [
                            (enterprise_number, line_id, current_schema_name)
                            for line_id in gsm_lines_in_schema
                        ]
                        cur.executemany(
                            "INSERT INTO gsm_outgoing_schema_assignments (enterprise_number, gsm_line_id, schema_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            assignment_data
                        )

                # --- Основное обновление схемы ---
                cur.execute(
                    "UPDATE dial_schemas SET schema_name = %s, schema_data = %s, updated_at = NOW() WHERE schema_id = %s",
                    (current_schema_name, json.dumps(schema_update.schema_data.dict()), schema_id)
                )
                logger.info(f"Successfully updated schema {schema_id}")

                # --- НАЧАЛО ДОПОЛНИТЕЛЬНОГО БЛОКА ---
                nodes = schema_update.schema_data.nodes

                if schema_update.schema_type == 'outgoing':
                    # Обработка исходящих схем (привязка к GSM/SIP линиям)
                    logger.info(f"Processing MULTIPLE OUTGOING schema assignments for schema_id: {schema_id}")
                    
                    gsm_line_ids = []
                    sip_line_ids = []

                    for node in nodes:
                        if node.get('type') == 'externalLines' and node.get('data') and isinstance(node['data'].get('external_lines'), list):
                            for line in node['data']['external_lines']:
                                line_id = line.get('line_id')
                                if line_id:
                                    if line_id.startswith('gsm_'):
                                        gsm_line_ids.append(line_id.replace('gsm_', ''))
                                    elif line_id.startswith('sip_'):
                                        sip_line_ids.append(line_id.replace('sip_', ''))
                    
                    # ИСПРАВЛЕНО: Используем schema_name для удаления
                    current_schema_name = schema_update.schema_name
                    cur.execute("DELETE FROM gsm_outgoing_schema_assignments WHERE schema_name = %s AND enterprise_number = %s", (current_schema_name, enterprise_number))
                    cur.execute("DELETE FROM sip_outgoing_schema_assignments WHERE schema_name = %s AND enterprise_number = %s", (current_schema_name, enterprise_number))

                    if gsm_line_ids:
                        # ИСПРАВЛЕНО: Вставляем schema_name вместо schema_id
                        gsm_values = [(enterprise_number, gsm_id, current_schema_name) for gsm_id in set(gsm_line_ids)]
                        execute_values(cur, "INSERT INTO gsm_outgoing_schema_assignments (enterprise_number, gsm_line_id, schema_name) VALUES %s", gsm_values)
                        logger.info(f"Inserted {len(gsm_values)} GSM line assignments for multiple outgoing.")
                    
                    if sip_line_ids:
                        # ИСПРАВЛЕНО: Вставляем schema_name и enterprise_number
                        # Предполагаем, что структура sip_outgoing_schema_assignments аналогична gsm_...
                        sip_values = [(enterprise_number, sip_id, current_schema_name) for sip_id in set(sip_line_ids)]
                        execute_values(cur, "INSERT INTO sip_outgoing_schema_assignments (enterprise_number, sip_line_name, schema_name) VALUES %s", sip_values)
                        logger.info(f"Inserted {len(sip_values)} SIP line assignments for multiple outgoing.")

                elif schema_update.schema_type == 'incoming':
                    # Обработка входящих схем (привязка к личным/внутренним номерам)
                    logger.info(f"Processing MULTIPLE INCOMING schema assignments for schema_id: {schema_id}")

                    user_ids = set()
                    internal_phone_numbers = set()

                    for node in nodes:
                        if node.get('type') == 'dial' and node.get('data') and isinstance(node['data'].get('managers'), list):
                            for manager in node['data']['managers']:
                                if manager.get('userId'):
                                    user_ids.add(manager['userId'])
                                # Собираем все внутренние номера из узлов dial
                                if manager.get('phone'):
                                    internal_phone_numbers.add(manager['phone'])
                    
                    cur.execute("DELETE FROM user_personal_phone_incoming_assignments WHERE schema_id = %s", (schema_id,))
                    if user_ids:
                        user_values = [(schema_id, user_id, schema_update.schema_name, enterprise_number) for user_id in user_ids]
                        execute_values(cur, "INSERT INTO user_personal_phone_incoming_assignments (schema_id, user_id, schema_name, enterprise_number) VALUES %s", user_values)
                        logger.info(f"Inserted {len(user_values)} personal phone (user) assignments for multiple incoming.")

                    # Для внутренних номеров нам нужны их ID
                    cur.execute("DELETE FROM user_internal_phone_incoming_assignments WHERE schema_id = %s", (schema_id,))
                    if internal_phone_numbers:
                        cur.execute("SELECT id, phone_number FROM user_internal_phones WHERE enterprise_number = %s AND phone_number = ANY(%s::varchar[])", (enterprise_number, list(internal_phone_numbers)))
                        phone_map = {row['phone_number']: row['id'] for row in cur.fetchall()}
                        
                        internal_values = []
                        for phone_num in internal_phone_numbers:
                            if phone_num in phone_map:
                                internal_values.append((schema_id, phone_map[phone_num], schema_update.schema_name, enterprise_number))
                        
                        if internal_values:
                            execute_values(cur, "INSERT INTO user_internal_phone_incoming_assignments (schema_id, internal_phone_id, schema_name, enterprise_number) VALUES %s", internal_values)
                            logger.info(f"Inserted {len(internal_values)} internal phone assignments for multiple incoming.")
                # --- КОНЕЦ ДОПОЛНИТЕЛЬНОГО БЛОКА ---

            conn.commit()

            # После успешного обновления, вызываем plan.py для генерации конфига
            with get_db_connection() as conn_for_plan:
                with conn_for_plan.cursor() as cur_for_plan:
                    # 1. Получаем ID предприятия по его номеру
                    cur_for_plan.execute("SELECT id FROM enterprises WHERE number = %s", (enterprise_number,))
                    enterprise_id_record = cur_for_plan.fetchone()
                    
                    if enterprise_id_record:
                        enterprise_id_to_send = enterprise_id_record[0]
                        # 2. Вызываем генерацию конфига с правильным ID
                        try:
                            response = requests.post(
                                'http://127.0.0.1:8006/generate_config',
                                json={'enterprise_id': enterprise_id_to_send}
                            )
                            if response.status_code == 200:
                                logger.info(f"Успешно вызван сервис генерации конфига для предприятия number={enterprise_number}, id={enterprise_id_to_send}.")
                                
                                # Проверяем результат развертывания
                                try:
                                    result = response.json()
                                    deployment_info = result.get("deployment", {})
                                    deployment_success = deployment_info.get("success", False)
                                    deployment_message = deployment_info.get("message", "")
                                    
                                    if not deployment_success:
                                        logger.warning(f"Схема обновлена локально, но не развернута на АТС для предприятия {enterprise_number}: {deployment_message}")
                                        # Здесь можно добавить уведомление пользователю через WebSocket или другой механизм
                                    else:
                                        logger.info(f"Схема успешно развернута на АТС для предприятия {enterprise_number}")
                                except Exception as json_error:
                                    logger.error(f"Ошибка при разборе ответа от сервиса генерации конфига: {json_error}")
                            else:
                                logger.error(f"Ошибка вызова сервиса генерации конфига для {enterprise_number}: {response.text}")
                        except requests.exceptions.RequestException as e:
                            logger.error(f"Не удалось подключиться к сервису генерации конфига: {e}")
                    else:
                        logger.error(f"Не удалось найти ID для предприятия с номером {enterprise_number}. Генерация конфига не вызвана.")


        # Возвращаем обновленную модель
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM dial_schemas WHERE schema_id = %s", (schema_id,))
                updated_schema_record = cur.fetchone()

        if not updated_schema_record:
            raise HTTPException(status_code=404, detail="Schema not found after update")
        
        # --- Исправление: Конвертируем datetime в строку ---
        response_data = dict(updated_schema_record)
        if 'created_at' in response_data and hasattr(response_data['created_at'], 'isoformat'):
            response_data['created_at'] = response_data['created_at'].isoformat()
        if 'updated_at' in response_data and hasattr(response_data['updated_at'], 'isoformat'):
            response_data['updated_at'] = response_data['updated_at'].isoformat()

        return SchemaModel(**response_data)

    except Exception as e:
        logger.error(f"Error updating schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/enterprises/{enterprise_number}/schemas/{schema_id}")
async def delete_schema(enterprise_number: str, schema_id: str):
    logger.info(f"Attempting to delete schema {schema_id} for enterprise {enterprise_number}")
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                
                # Шаг 1: Получить имя схемы перед удалением
                logger.info(f"Fetching schema name for schema_id: {schema_id}")
                cur.execute("SELECT schema_name FROM dial_schemas WHERE schema_id = %s", (schema_id,))
                schema_record = cur.fetchone()

                if not schema_record:
                    logger.warning(f"Schema {schema_id} not found when trying to get its name.")
                    # Если схемы нет, то и удалять нечего, но для клиента это выглядит как 404
                    raise HTTPException(status_code=404, detail="Schema not found")

                schema_name = schema_record['schema_name']
                logger.info(f"Schema name is '{schema_name}'. Proceeding to clear references.")

                # Шаг 2: Очистить ссылки на эту схему в gsm_lines, используя ИМЯ
                logger.info(f"Clearing gsm_lines references for schema_name '{schema_name}'")
                clear_gsm_sql = "UPDATE gsm_lines SET in_schema = NULL WHERE in_schema = %s AND enterprise_number = %s"
                cur.execute(clear_gsm_sql, (schema_name, enterprise_number))

                # Шаг 3: Очистить ссылки на эту схему в sip_unit, используя ИМЯ
                logger.info(f"Clearing sip_unit references for schema_name '{schema_name}'")
                # Добавим проверку на существование таблицы/колонки для большей надежности
                cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name='sip_unit' AND column_name='in_schema');")
                if cur.fetchone()[0]:
                    clear_sip_sql = "UPDATE sip_unit SET in_schema = NULL WHERE in_schema = %s AND enterprise_number = %s"
                    cur.execute(clear_sip_sql, (schema_name, enterprise_number))
                else:
                    logger.info("sip_unit.in_schema column does not exist, skipping cleanup.")

                # Шаг 4: Удалить саму схему
                logger.info(f"Deleting schema {schema_id} itself")
                delete_schema_sql = "DELETE FROM dial_schemas WHERE schema_id = %s AND enterprise_id = %s"
                cur.execute(delete_schema_sql, (schema_id, enterprise_number))
                
                if cur.rowcount == 0:
                    # Эта ситуация маловероятна, т.к. мы уже проверяли наличие схемы, но оставим на всякий случай
                    logger.warning(f"Schema {schema_id} not found for enterprise {enterprise_number} or already deleted during the process.")
                    raise HTTPException(status_code=404, detail="Schema not found or was deleted during operation")
                
                conn.commit()
        
        logger.info(f"Successfully deleted schema {schema_id} and all its references")
        return JSONResponse(status_code=200, content={"message": "Schema deleted successfully"})

    except psycopg2.Error as e:
        logger.error(f"Database error while deleting schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while deleting schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.get("/api/templates", response_model=List[MobileTemplate])
async def get_mobile_templates():
    """
    Fetches all templates from the 'mobile' table.
    """
    logger.info("Fetching all mobile templates.")
    query = "SELECT id, name, shablon FROM mobile ORDER BY name;"
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query)
                results = cur.fetchall()
        logger.info(f"Found {len(results)} mobile templates.")
        # Преобразуем DictRow в стандартные словари для корректной валидации FastAPI
        return [dict(row) for row in results]
    except psycopg2.Error as e:
        logger.error(f"Database error while fetching mobile templates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

# --- Static Files & React App ---
# Mount the static directory for the React build
static_files_path = os.path.join(os.path.dirname(__file__), "dial_frontend/dist")

# Ensure the static directory and index.html exist
os.makedirs(static_files_path, exist_ok=True)
if not os.path.exists(os.path.join(static_files_path, "index.html")):
    with open(os.path.join(static_files_path, "index.html"), "w") as f:
        f.write('<!DOCTYPE html><html><head></head><body><div id="root"></div></body></html>')

# Mount the 'assets' directory for JS/CSS files
app.mount("/assets", StaticFiles(directory=os.path.join(static_files_path, "assets")), name="assets")
app.mount("/static", StaticFiles(directory="dial_frontend/dist/assets"), name="static")
templates = Jinja2Templates(directory="dial_frontend/dist")

@app.get("/{full_path:path}")
async def serve_react_app(request: Request, full_path: str):
    """
    Serves the React app's index.html for any non-API path.
    Adds headers to prevent caching of the index.html file.
    """
    # API-запросы не должны обрабатываться этим обработчиком,
    # но это дополнительная проверка.
    if full_path.startswith("api/"):
        return JSONResponse(
            status_code=404,
            content={"detail": "API endpoint not found here."},
        )

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return FileResponse(os.path.join(static_files_path, "index.html"), headers=headers)

@app.get("/api/enterprises/{enterprise_number}/music-files", response_model=List[MusicFile])
async def get_enterprise_music_files(enterprise_number: str):
    logger.info(f"Fetching music files for enterprise_number: {enterprise_number}")
    base_dir = os.path.abspath("music")
    enterprise_dir = os.path.join(base_dir, enterprise_number, "start")
    
    music_files = []
    if os.path.exists(enterprise_dir) and os.path.isdir(enterprise_dir):
        for index, filename in enumerate(os.listdir(enterprise_dir)):
            if filename.endswith(".wav"):
                music_files.append({"id": index, "display_name": filename})
    
    logger.info(f"Found {len(music_files)} music files for enterprise {enterprise_number}")
    return music_files

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)