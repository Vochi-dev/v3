import os
import json
import uuid
import psycopg2
import logging
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from dotenv import load_dotenv
from datetime import datetime
from psycopg2.extras import DictCursor
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

class SchemaUpdateModel(BaseModel):
    schema_name: str
    schema_data: SchemaDataModel

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
    GSM и SIP линии исключены.
    """
    logger.info(f"Fetching all numbers and users for enterprise_number: {enterprise_number}")
    
    # Запрос для пользователей и их внутренних номеров
    users_query = """
    SELECT 
        u.id as user_id, 
        (u.first_name || ' ' || u.last_name) AS full_name,
        p.phone_number,
        'internal' as type
    FROM users u
    JOIN user_internal_phones p ON u.id = p.user_id
    WHERE u.enterprise_number = %s;
    """

    # Запрос для внутренних номеров без пользователей
    unassigned_internal_query = """
    SELECT
        null as user_id,
        null as full_name,
        p.phone_number,
        'internal' as type
    FROM user_internal_phones p
    WHERE p.enterprise_number = %s AND p.user_id IS NULL;
    """

    # Запрос для личных мобильных номеров сотрудников
    personal_phones_query = """
    SELECT
        u.id as user_id,
        (u.first_name || ' ' || u.last_name) AS full_name,
        u.personal_phone as phone_number,
        'personal' as type
    FROM users u
    WHERE u.enterprise_number = %s AND u.personal_phone IS NOT NULL AND u.personal_phone <> '';
    """
    
    all_numbers = []
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Пользователи с внутренними номерами
                cur.execute(users_query, (enterprise_number,))
                all_numbers.extend(cur.fetchall())
                
                # Внутренние без пользователей
                cur.execute(unassigned_internal_query, (enterprise_number,))
                all_numbers.extend(cur.fetchall())

                # Личные мобильные номера
                cur.execute(personal_phones_query, (enterprise_number,))
                all_numbers.extend(cur.fetchall())
                
                # Преобразуем в корректный формат для фронтенда
                results = [
                    {
                        "user_id": row[0],
                        "full_name": row[1],
                        "phone_number": row[2],
                        "is_internal": row[3] == 'internal'
                    }
                    for row in all_numbers
                ]
                
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

# --- REFACTORED: API for Schemas ---
@app.get("/api/enterprises/{enterprise_number}/schemas", response_model=List[SchemaModel])
async def get_schemas_list(enterprise_number: str):
    logger.info(f"Fetching schemas from DB for enterprise_number: {enterprise_number}")
    query = "SELECT * FROM dial_schemas WHERE enterprise_id = %s ORDER BY created_at;"
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (enterprise_number,))
                schemas = cur.fetchall()
        
        # Преобразуем created_at в строку ISO, если это необходимо
        result_schemas = []
        for s in schemas:
            schema_dict = dict(s)
            if 'created_at' in schema_dict and hasattr(schema_dict['created_at'], 'isoformat'):
                schema_dict['created_at'] = schema_dict['created_at'].isoformat()
            result_schemas.append(SchemaModel(**schema_dict))

        logger.info(f"Found {len(result_schemas)} schemas in DB for enterprise {enterprise_number}")
        return result_schemas
    except psycopg2.Error as e:
        logger.error(f"DB error fetching schemas for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while fetching schemas.")

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
async def create_schema(enterprise_number: str, schema_in: SchemaUpdateModel):
    new_schema = SchemaModel(
        enterprise_id=enterprise_number,
        schema_name=schema_in.schema_name,
        schema_data=schema_in.schema_data
    )
    
    # Конвертируем Pydantic модель в JSON-строку для PostgreSQL
    schema_data_json = json.dumps(new_schema.schema_data.dict())

    query = """
        INSERT INTO dial_schemas (schema_id, enterprise_id, schema_name, schema_data, created_at)
        VALUES (%s, %s, %s, %s, %s)
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
                    datetime.fromisoformat(new_schema.created_at)
                ))
                created_record = cur.fetchone()
                conn.commit()
        
        if not created_record:
            raise HTTPException(status_code=500, detail="Failed to create schema in DB.")

        logger.info(f"Schema created in DB with id: {new_schema.schema_id}")
        
        # Преобразуем для ответа
        response_schema = dict(created_record)
        response_schema['created_at'] = response_schema['created_at'].isoformat()
        return SchemaModel(**response_schema)

    except psycopg2.Error as e:
        logger.error(f"DB error creating schema for {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while creating schema.")

@app.put("/api/enterprises/{enterprise_number}/schemas/{schema_id}", response_model=SchemaModel)
async def update_schema(enterprise_number: str, schema_id: str, schema_update: SchemaUpdateModel):
    logger.info(f"Updating schema in DB: {schema_id}")
    
    schema_data_json = json.dumps(schema_update.schema_data.dict())
    
    query = """
        UPDATE dial_schemas
        SET schema_name = %s, schema_data = %s
        WHERE schema_id = %s AND enterprise_id = %s
        RETURNING *;
    """
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (
                    schema_update.schema_name,
                    schema_data_json,
                    schema_id,
                    enterprise_number
                ))
                updated_record = cur.fetchone()
                conn.commit()

        if not updated_record:
            logger.warning(f"Schema not found in DB for update: {schema_id}")
            raise HTTPException(status_code=404, detail="Schema not found")

        logger.info(f"Schema updated in DB: {schema_id}")
        
        response_schema = dict(updated_record)
        response_schema['created_at'] = response_schema['created_at'].isoformat()
        return SchemaModel(**response_schema)
        
    except psycopg2.Error as e:
        logger.error(f"DB error updating schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while updating schema.")

@app.delete("/api/enterprises/{enterprise_number}/schemas/{schema_id}", status_code=204)
async def delete_schema(enterprise_number: str, schema_id: str):
    logger.info(f"Attempting to delete schema from DB: {schema_id}")
    
    # Сначала нужно получить схему, чтобы проверить зависимости
    get_schema_query = "SELECT schema_name, schema_data FROM dial_schemas WHERE schema_id = %s AND enterprise_id = %s;"
    schema_name = None
    schema_data = None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(get_schema_query, (schema_id, enterprise_number))
                result = cur.fetchone()
                if result:
                    schema_name = result[0]
                    schema_data = result[1]
    except psycopg2.Error as e:
        logger.error(f"DB error getting schema name for {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при получении имени схемы.")

    if not schema_name:
        raise HTTPException(status_code=404, detail="Schema not found to get name for checks.")

    # Новая проверка для исходящих схем
    if schema_name.startswith('Исходящая') and schema_data:
        nodes = schema_data.get('nodes', [])
        outgoing_node = next((node for node in nodes if node.get('type') == 'outgoing-call'), None)
        if outgoing_node and outgoing_node.get('data', {}).get('phones'):
            raise HTTPException(
                status_code=409, # Conflict
                detail="Невозможно удалить схему, так как в узле 'Исходящий звонок' выбраны номера. Сначала отвяжите их."
            )

    # Проверка привязанных линий в PostgreSQL
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM gsm_lines WHERE enterprise_number = %s AND in_schema = %s LIMIT 1",
                    (enterprise_number, schema_name)
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=409, # Conflict
                        detail="Невозможно удалить схему, так как к ней привязаны GSM линии. Сначала отвяжите их."
                    )
                
                cur.execute(
                    "SELECT 1 FROM sip_unit WHERE enterprise_number = %s AND in_schema = %s LIMIT 1",
                    (enterprise_number, schema_name)
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=409, # Conflict
                        detail="Невозможно удалить схему, так как к ней привязаны SIP линии. Сначала отвяжите их."
                    )
    except psycopg2.Error as e:
        logger.error(f"Database error while checking assigned lines for schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при проверке привязанных линий.")

    # Если проверки пройдены, удаляем схему из БД
    delete_query = "DELETE FROM dial_schemas WHERE schema_id = %s AND enterprise_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(delete_query, (schema_id, enterprise_number))
                # Проверяем, была ли строка на самом деле удалена
                if cur.rowcount == 0:
                    # Этого не должно произойти, если мы дошли сюда, но для надежности
                    logger.warning(f"Schema not found in DB for deletion, though it existed for checks: {schema_id}")
                    raise HTTPException(status_code=404, detail="Schema not found for deletion.")
                conn.commit()
        logger.info(f"Successfully deleted schema from DB: {schema_id}")
    except psycopg2.Error as e:
        logger.error(f"DB error deleting schema {schema_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while deleting schema.")
    
    return

# --- Static Files Serving ---
STATIC_DIR = "dial_frontend/dist"
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")

# Ensure the static directory and index.html exist
os.makedirs(STATIC_DIR, exist_ok=True)
if not os.path.exists(INDEX_PATH):
    with open(INDEX_PATH, "w") as f:
        f.write('<!DOCTYPE html><html><head></head><body><div id="root"></div></body></html>')

# Mount the 'assets' directory for JS/CSS files
app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")
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
    return FileResponse(INDEX_PATH, headers=headers)

# Если у вас будут API-ручки для взаимодействия с фронтендом, они будут здесь
# Например:
# @app.get("/api/v1/schemas/{schema_id}")
# async def get_schema(schema_id: int):
#     # Здесь будет логика получения схемы из базы данных
#     return {"schema_id": schema_id, "data": "..."}

@app.get("/api/enterprises/{enterprise_number}/music-files", response_model=List[MusicFile])
async def get_enterprise_music_files(enterprise_number: str):
    query = """
        SELECT id, display_name FROM music_files
        WHERE enterprise_number = %s AND file_type = 'hold'
        ORDER BY display_name;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query, (enterprise_number,))
                files = [MusicFile(id=row['id'], display_name=row['display_name']) for row in cur.fetchall()]
                return files
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=500, detail="Failed to fetch music files")

# --- API for Templates ---
@app.get("/api/templates", response_model=List[MobileTemplate])
async def get_mobile_templates():
    """
    Fetches all templates from the 'mobile' table.
    """
    logger.info("Fetching all mobile templates")
    query = "SELECT id, name, shablon FROM mobile ORDER BY id;"
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(query)
                templates = cur.fetchall()
                logger.info(f"Found {len(templates)} mobile templates.")
                return [MobileTemplate(**t) for t in templates]
    except psycopg2.Error as e:
        logger.error(f"Database error while fetching mobile templates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}") 