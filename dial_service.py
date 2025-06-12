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
DB_FILE = "schemas_db.json"
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

# --- JSON DB for Schemas ---
def read_db():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def write_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

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

@app.put("/api/enterprises/{enterprise_number}/schemas/{schema_id}/assign_lines")
async def assign_lines_to_schema(enterprise_number: str, schema_id: str, line_ids: List[str] = Body(...)):
    """
    Assigns a list of lines to a schema, clearing old assignments for THIS schema.
    It will overwrite assignments from other schemas.
    """
    db = read_db()
    try:
        schema_name = [s['schema_name'] for s in db if s['schema_id'] == schema_id and s['enterprise_id'] == enterprise_number][0]
    except IndexError:
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
    db = read_db()
    enterprise_schemas = [SchemaModel(**s) for s in db if s.get('enterprise_id') == enterprise_number]
    return enterprise_schemas

@app.get("/api/enterprises/{enterprise_number}/schemas/{schema_id}", response_model=SchemaModel)
async def get_schema_by_id(enterprise_number: str, schema_id: str):
    db = read_db()
    schema = next((s for s in db if s.get('schema_id') == schema_id and s.get('enterprise_id') == enterprise_number), None)
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    return SchemaModel(**schema)

@app.post("/api/enterprises/{enterprise_number}/schemas", response_model=SchemaModel)
async def create_schema(enterprise_number: str, schema_in: SchemaUpdateModel):
    db = read_db()
    
    new_schema = SchemaModel(
        enterprise_id=enterprise_number,
        schema_name=schema_in.schema_name,
        schema_data=schema_in.schema_data
    )
    
    db.append(new_schema.dict())
    write_db(db)
    return new_schema

@app.put("/api/enterprises/{enterprise_number}/schemas/{schema_id}", response_model=SchemaModel)
async def update_schema(enterprise_number: str, schema_id: str, schema_update: SchemaUpdateModel):
    db = read_db()
    schema_index = -1
    for i, s in enumerate(db):
        if s.get('schema_id') == schema_id and s.get('enterprise_id') == enterprise_number:
            schema_index = i
            break
            
    if schema_index == -1:
        raise HTTPException(status_code=404, detail="Schema not found")
        
    existing_schema = db[schema_index]
    updated_data = schema_update.dict(exclude_unset=True)
    
    # Обновляем только переданные поля
    existing_schema['schema_name'] = updated_data.get('schema_name', existing_schema['schema_name'])
    existing_schema['schema_data'] = updated_data.get('schema_data', existing_schema['schema_data'])
    
    db[schema_index] = existing_schema
    write_db(db)
    return SchemaModel(**existing_schema)

@app.delete("/api/enterprises/{enterprise_number}/schemas/{schema_id}", status_code=204)
async def delete_schema(enterprise_number: str, schema_id: str):
    db = read_db()
    
    initial_len = len(db)
    db = [s for s in db if not (s.get('schema_id') == schema_id and s.get('enterprise_id') == enterprise_number)]
    
    if len(db) == initial_len:
        raise HTTPException(status_code=404, detail="Schema not found")
        
    write_db(db)
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