"""
Сервис логирования звонков - logger.py
Порт: 8026

Централизованный сервис для логирования всех событий звонков
в структурированном виде с возможностью быстрого поиска и анализа.
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import uvicorn
from datetime import datetime, timedelta, timezone
import json

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Call Logger Service",
    description="Сервис логирования звонков",
    version="1.0.0"
)

# Настройка шаблонов
templates = Jinja2Templates(directory="templates/call_viewer")

# ═══════════════════════════════════════════════════════════════════
# МОДЕЛИ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════

class CallEvent(BaseModel):
    """Модель события звонка"""
    enterprise_number: str
    unique_id: str
    event_type: str  # dial, bridge, hangup, etc.
    event_data: Dict[str, Any]
    chat_id: Optional[int] = None  # ID получателя в Telegram
    phone_number: Optional[str] = None  # Номер телефона
    bridge_unique_id: Optional[str] = None  # BridgeUniqueid для группировки
    timestamp: Optional[datetime] = None

class HttpRequest(BaseModel):
    """Модель HTTP запроса"""
    enterprise_number: str
    unique_id: str
    method: str
    url: str
    request_data: Optional[Dict[str, Any]] = None
    response_data: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None
    duration_ms: Optional[float] = None
    timestamp: Optional[datetime] = None

class SqlQuery(BaseModel):
    """Модель SQL запроса"""
    enterprise_number: str
    unique_id: str
    query: str
    parameters: Optional[List[Any]] = None
    result: Optional[Any] = None
    duration_ms: Optional[float] = None
    timestamp: Optional[datetime] = None

class TelegramMessage(BaseModel):
    """Модель Telegram сообщения"""
    enterprise_number: str
    unique_id: str
    chat_id: int
    message_type: str  # dial, bridge, hangup
    message_id: Optional[int] = None
    message_text: Optional[str] = None
    action: str  # send, edit, delete
    timestamp: Optional[datetime] = None

class IntegrationResponse(BaseModel):
    """Модель ответа интеграции"""
    enterprise_number: str
    unique_id: str
    integration: str  # moysklad, retailcrm, etc.
    endpoint: str
    method: str
    status: str  # success, error, etc.
    request_data: Optional[Dict[str, Any]] = None
    response_data: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    timestamp: Optional[datetime] = None

# ═══════════════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════

import asyncpg
import os

# Параметры подключения к БД
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "postgres", 
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg=="
}

# Connection pool (глобальный пул соединений)
db_pool: Optional[asyncpg.Pool] = None

# Кэш существующих партиций (в памяти) - чтобы не проверять каждый раз в БД
partition_cache = set()

async def init_db_pool():
    """Инициализация connection pool при старте сервиса"""
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            **DB_CONFIG,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
        logger.info("✅ Database connection pool initialized (5-20 connections)")
    return db_pool

async def close_db_pool():
    """Закрытие connection pool при остановке сервиса"""
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None
        logger.info("✅ Database connection pool closed")

@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    await init_db_pool()
    await load_existing_partitions()

@app.on_event("shutdown")
async def shutdown_event():
    """Очистка при остановке"""
    await close_db_pool()

async def load_existing_partitions():
    """Загрузка списка существующих партиций в кэш при старте"""
    global partition_cache
    try:
        async with db_pool.acquire() as conn:
            query = """
            SELECT tablename FROM pg_tables 
            WHERE schemaname = 'public' 
            AND tablename ~ '^[0-9]{4}$'
            """
            rows = await conn.fetch(query)
            partition_cache = {row['tablename'] for row in rows}
            logger.info(f"✅ Loaded {len(partition_cache)} existing partitions into cache")
    except Exception as e:
        logger.error(f"❌ Error loading partitions into cache: {e}")

# ═══════════════════════════════════════════════════════════════════
# ФУНКЦИИ УПРАВЛЕНИЯ ПАРТИЦИЯМИ
# ═══════════════════════════════════════════════════════════════════

async def ensure_enterprise_partition(enterprise_number: str):
    """Автоматически создает партицию для предприятия если её нет (с кэшем)"""
    global partition_cache
    
    # ОПТИМИЗАЦИЯ: Быстрая проверка в кэше (без запроса к БД!)
    if enterprise_number in partition_cache:
        return True
    
    try:
        # Партиции нет в кэше - используем connection pool
        async with db_pool.acquire() as conn:
            partition_name = enterprise_number
            
            check_query = """
            SELECT EXISTS (
                SELECT 1 FROM pg_tables 
                WHERE tablename = $1 AND schemaname = 'public'
            )
            """
            
            exists = await conn.fetchval(check_query, partition_name)
            
            if not exists:
                # Создаем LIST партицию с простым названием
                create_query = f"""
                CREATE TABLE "{partition_name}" PARTITION OF call_traces 
                FOR VALUES IN ('{enterprise_number}')
                """
                
                await conn.execute(create_query)
                
                # Добавляем UNIQUE constraint
                constraint_query = f"""
                ALTER TABLE "{partition_name}" 
                ADD CONSTRAINT unique_call_trace_{enterprise_number} 
                UNIQUE (unique_id, enterprise_number)
                """
                
                await conn.execute(constraint_query)
                
                logger.info(f"✅ Created LIST partition {partition_name} for enterprise {enterprise_number}")
            
            # Добавляем в кэш чтобы больше не проверять
            partition_cache.add(enterprise_number)
            return True
        
    except Exception as e:
        logger.error(f"❌ Error ensuring partition for enterprise {enterprise_number}: {e}")
        return False

async def create_enterprise_partition_api(enterprise_number: str):
    """API функция для создания партиции предприятия"""
    try:
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            partition_name = f"call_traces_{enterprise_number}"
            remainder_val = int(enterprise_number)
            
            create_query = f"""
            CREATE TABLE {partition_name} PARTITION OF call_traces 
            FOR VALUES WITH (MODULUS 1000, REMAINDER {remainder_val})
            """
            
            await conn.execute(create_query)
        
        logger.info(f"✅ Created partition {partition_name}")
        return {"status": "success", "partition": partition_name}
        
    except Exception as e:
        logger.error(f"❌ Error creating partition: {e}")
        return {"status": "error", "message": str(e)}

async def drop_enterprise_partition_api(enterprise_number: str):
    """API функция для удаления партиции предприятия"""
    try:
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            partition_name = f"call_traces_{enterprise_number}"
            
            # Сначала проверяем есть ли данные
            count_query = f"SELECT COUNT(*) FROM {partition_name}"
            count = await conn.fetchval(count_query)
            
            if count > 0:
                return {"status": "error", "message": f"Partition contains {count} records. Use force=true to delete anyway."}
            
            # Удаляем партицию
            drop_query = f"DROP TABLE {partition_name}"
            await conn.execute(drop_query)
        
        logger.info(f"✅ Dropped partition {partition_name}")
        return {"status": "success", "partition": partition_name}
        
    except Exception as e:
        logger.error(f"❌ Error dropping partition: {e}")
        return {"status": "error", "message": str(e)}

async def list_enterprise_partitions():
    """Список всех партиций предприятий"""
    try:
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            query = """
            SELECT 
                tablename,
                pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                (SELECT COUNT(*) FROM information_schema.tables t2 
                 WHERE t2.table_name = t.tablename) as record_count
            FROM pg_tables t
            WHERE tablename LIKE 'call_traces_%' 
            AND tablename != 'call_traces_default'
            ORDER BY tablename
            """
            
            partitions = await conn.fetch(query)
        
        result = []
        for partition in partitions:
            enterprise_num = partition['tablename'].replace('call_traces_', '')
            result.append({
                "enterprise_number": enterprise_num,
                "partition_name": partition['tablename'],
                "size": partition['size'],
                "estimated_records": partition['record_count']
            })
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error listing partitions: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Главная страница сервиса"""
    return {
        "service": "Call Logger",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "log_event": "POST /log/event",
            "log_http": "POST /log/http", 
            "log_sql": "POST /log/sql",
            "log_telegram": "POST /log/telegram",
            "get_trace": "GET /trace/{unique_id}",
            "search": "GET /search"
        }
    }

@app.post("/log/event")
async def log_call_event(event: CallEvent):
    """Логирование события звонка"""
    try:
        if not event.timestamp:
            event.timestamp = datetime.now()
            
        # Автоматически создаем партицию для предприятия если её нет
        await ensure_enterprise_partition(event.enterprise_number)
        
        # ОПТИМИЗАЦИЯ: Используем connection pool вместо создания нового соединения
        async with db_pool.acquire() as conn:
            # Извлекаем номер телефона из данных события если есть
            phone_number = event.phone_number
            if not phone_number:
                if 'Phone' in event.event_data:
                    phone_number = event.event_data['Phone']
                elif 'phone' in event.event_data:
                    phone_number = event.event_data['phone']
            
            # Извлекаем BridgeUniqueid из данных события если не передан явно
            bridge_unique_id = event.bridge_unique_id
            if not bridge_unique_id and 'BridgeUniqueid' in event.event_data:
                bridge_unique_id = event.event_data['BridgeUniqueid']
            
            # Добавляем chat_id в event_data для сохранения в JSONB
            event_data_with_chat = event.event_data.copy()
            if event.chat_id:
                event_data_with_chat["_chat_id"] = event.chat_id
            
            # Добавляем событие через функцию БД (теперь с bridge_unique_id)
            trace_id = await conn.fetchval(
                "SELECT add_call_event($1, $2, $3, $4, $5, $6)",
                event.unique_id,
                event.enterprise_number, 
                event.event_type,
                json.dumps(event_data_with_chat),
                phone_number,
                bridge_unique_id
            )
        
        chat_info = f" (chat_id: {event.chat_id})" if event.chat_id else ""
        logger.info(f"Logged event {event.event_type} for call {event.unique_id}{chat_info} (trace_id: {trace_id})")
        return {
            "status": "success", 
            "message": "Event logged",
            "trace_id": trace_id,
            "unique_id": event.unique_id
        }
        
    except Exception as e:
        logger.error(f"Error logging event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/log/http")
async def log_http_request(request: HttpRequest):
    """Логирование HTTP запроса"""
    try:
        if not request.timestamp:
            request.timestamp = datetime.now()
        
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Добавляем HTTP запрос через функцию БД
            await conn.execute(
                "SELECT add_http_request($1, $2, $3, $4, $5, $6, $7, $8)",
                request.unique_id,
                request.enterprise_number,
                request.method,
                request.url,
                json.dumps(request.request_data) if request.request_data else None,
                json.dumps(request.response_data) if request.response_data else None,
                request.status_code,
                request.duration_ms
            )
        
        logger.info(f"Logged HTTP {request.method} {request.url} for call {request.unique_id}")
        return {"status": "success", "message": "HTTP request logged"}
        
    except Exception as e:
        logger.error(f"Error logging HTTP request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/log/sql")
async def log_sql_query(query: SqlQuery):
    """Логирование SQL запроса"""
    try:
        if not query.timestamp:
            query.timestamp = datetime.now()
            
        unique_id = query.unique_id
        
        # Создаем партицию для предприятия, если её нет
        await ensure_enterprise_partition(query.enterprise_number)

        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Добавляем SQL запрос через функцию БД
            await conn.execute(
                "SELECT add_sql_query($1, $2, $3, $4, $5, $6)",
                query.unique_id,
                query.enterprise_number,
                query.query,
                json.dumps(query.parameters) if query.parameters else None,
                json.dumps(query.result) if query.result else None,
                query.duration_ms
            )
        
        logger.info(f"Logged SQL query for call {unique_id}")
        return {"status": "success", "message": "SQL query logged"}
        
    except Exception as e:
        logger.error(f"Error logging SQL query: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/log/telegram")
async def log_telegram_message(message: TelegramMessage):
    """Логирование Telegram сообщения"""
    try:
        if not message.timestamp:
            message.timestamp = datetime.now()
            
        unique_id = message.unique_id
        
        # Создаем партицию для предприятия, если её нет
        await ensure_enterprise_partition(message.enterprise_number)

        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Добавляем Telegram сообщение через функцию БД
            await conn.execute(
                "SELECT add_telegram_message($1, $2, $3, $4, $5, $6, $7, $8)",
                message.unique_id,
                message.enterprise_number,
                message.chat_id,
                message.message_type,
                message.action,
                message.message_id,
                message.message_text,
                None  # error
            )
        
        logger.info(f"Logged Telegram {message.action} for call {unique_id}")
        return {"status": "success", "message": "Telegram message logged"}
        
    except Exception as e:
        logger.error(f"Error logging Telegram message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/log/integration")
async def log_integration_response(response: IntegrationResponse):
    """Логирование ответа интеграции"""
    try:
        if not response.timestamp:
            response.timestamp = datetime.now()
            
        unique_id = response.unique_id
        
        # Создаем партицию для предприятия, если её нет
        await ensure_enterprise_partition(response.enterprise_number)

        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Добавляем ответ интеграции через функцию БД
            await conn.execute(
                "SELECT add_integration_response($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                response.unique_id,
                response.enterprise_number,
                response.integration,
                response.endpoint,
                response.method,
                response.status,
                json.dumps(response.request_data) if response.request_data else None,
                json.dumps(response.response_data) if response.response_data else None,
                response.duration_ms,
                response.error
            )
        
        logger.info(f"Logged integration response for call {unique_id}")
        return {"status": "success", "message": "Integration response logged"}
        
    except Exception as e:
        logger.error(f"Error logging integration response: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/trace/{unique_id}")
async def get_call_trace(unique_id: str):
    """Получение полного трейса звонка"""
    try:
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Получаем основную информацию о трейсе
            trace_info = await conn.fetchrow("""
                SELECT id, unique_id, enterprise_number, phone_number, call_direction, 
                       call_status, start_time, end_time, call_events,
                       http_requests, sql_queries, telegram_messages, integration_responses,
                       created_at, updated_at,
                       CASE 
                           WHEN end_time IS NOT NULL AND start_time IS NOT NULL 
                           THEN EXTRACT(EPOCH FROM (end_time - start_time))
                           ELSE NULL 
                       END as duration_seconds
                FROM call_traces 
                WHERE unique_id = $1
            """, unique_id)
            
            if not trace_info:
                raise HTTPException(status_code=404, detail="Call trace not found")
        
        # Формируем единый timeline из всех типов логов
        timeline = []
        
        # Добавляем события звонков
        if trace_info['call_events']:
            try:
                if isinstance(trace_info['call_events'], str):
                    events_list = json.loads(trace_info['call_events'])
                else:
                    events_list = trace_info['call_events']
                
                if isinstance(events_list, list):
                    for event in events_list:
                        timeline.append({
                            "type": "call_event",
                            "sequence": event.get('event_sequence', 0),
                            "event_type": event.get('event_type', 'unknown'),
                            "timestamp": event.get('event_timestamp', ''),
                            "data": event.get('event_data', {})
                        })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse call_events: {e}")
        
        # Добавляем HTTP запросы
        if trace_info['http_requests']:
            try:
                if isinstance(trace_info['http_requests'], str):
                    http_list = json.loads(trace_info['http_requests'])
                else:
                    http_list = trace_info['http_requests']
                
                if isinstance(http_list, list):
                    for req in http_list:
                        timeline.append({
                            "type": "http_request",
                            "sequence": req.get('sequence', 0),
                            "method": req.get('method', 'GET'),
                            "url": req.get('url', ''),
                            "status_code": req.get('status_code'),
                            "duration_ms": req.get('duration_ms'),
                            "timestamp": req.get('timestamp', ''),
                            "data": {
                                "request": req.get('request_data'),
                                "response": req.get('response_data'),
                                "error": req.get('error')
                            }
                        })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse http_requests: {e}")
        
        # Добавляем SQL запросы
        if trace_info['sql_queries']:
            try:
                if isinstance(trace_info['sql_queries'], str):
                    sql_list = json.loads(trace_info['sql_queries'])
                else:
                    sql_list = trace_info['sql_queries']
                
                if isinstance(sql_list, list):
                    for query in sql_list:
                        timeline.append({
                            "type": "sql_query",
                            "sequence": query.get('sequence', 0),
                            "query": query.get('query', ''),
                            "duration_ms": query.get('duration_ms'),
                            "timestamp": query.get('timestamp', ''),
                            "data": {
                                "parameters": query.get('parameters'),
                                "result": query.get('result'),
                                "error": query.get('error')
                            }
                        })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse sql_queries: {e}")
        
        # Добавляем Telegram сообщения
        if trace_info['telegram_messages']:
            try:
                if isinstance(trace_info['telegram_messages'], str):
                    tg_list = json.loads(trace_info['telegram_messages'])
                else:
                    tg_list = trace_info['telegram_messages']
                
                if isinstance(tg_list, list):
                    for msg in tg_list:
                        timeline.append({
                            "type": "telegram_message",
                            "sequence": msg.get('sequence', 0),
                            "action": msg.get('action', 'send'),
                            "chat_id": msg.get('chat_id'),
                            "message_id": msg.get('message_id'),
                            "timestamp": msg.get('timestamp', ''),
                            "data": {
                                "message_type": msg.get('message_type'),
                                "message_text": msg.get('message_text'),
                                "error": msg.get('error')
                            }
                        })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse telegram_messages: {e}")
        
        # Добавляем ответы интеграций
        if trace_info['integration_responses']:
            try:
                if isinstance(trace_info['integration_responses'], str):
                    int_list = json.loads(trace_info['integration_responses'])
                else:
                    int_list = trace_info['integration_responses']
                
                if isinstance(int_list, list):
                    for resp in int_list:
                        timeline.append({
                            "type": "integration_response",
                            "sequence": resp.get('sequence', 0),
                            "integration": resp.get('integration', ''),
                            "endpoint": resp.get('endpoint', ''),
                            "method": resp.get('method', 'POST'),
                            "status": resp.get('status', 'unknown'),
                            "duration_ms": resp.get('duration_ms'),
                            "timestamp": resp.get('timestamp', ''),
                            "data": {
                                "request": resp.get('request_data'),
                                "response": resp.get('response_data'),
                                "error": resp.get('error')
                            }
                        })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse integration_responses: {e}")
        
        # Сортируем timeline по временным меткам
        timeline.sort(key=lambda x: x.get('timestamp', ''))
        
        return {
            "unique_id": unique_id,
            "enterprise_number": trace_info['enterprise_number'],
            "phone_number": trace_info['phone_number'],
            "call_direction": trace_info['call_direction'],
            "call_status": trace_info['call_status'],
            "start_time": trace_info['start_time'].isoformat() if trace_info['start_time'] else None,
            "end_time": trace_info['end_time'].isoformat() if trace_info['end_time'] else None,
            "duration_seconds": float(trace_info['duration_seconds']) if trace_info['duration_seconds'] else None,
            "created_at": trace_info['created_at'].isoformat() if trace_info['created_at'] else None,
            "updated_at": trace_info['updated_at'].isoformat() if trace_info['updated_at'] else None,
            "timeline": timeline,
            "summary": {
                "total_events": len([t for t in timeline if t['type'] == 'call_event']),
                "http_requests": len([t for t in timeline if t['type'] == 'http_request']),
                "sql_queries": len([t for t in timeline if t['type'] == 'sql_query']),
                "telegram_messages": len([t for t in timeline if t['type'] == 'telegram_message']),
                "integration_responses": len([t for t in timeline if t['type'] == 'integration_response']),
                "total_timeline_entries": len(timeline)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting call trace: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search")
async def search_traces(
    enterprise: Optional[str] = Query(None, description="Номер предприятия"),
    phone: Optional[str] = Query(None, description="Номер телефона"),
    date_from: Optional[str] = Query(None, description="Дата от (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Дата до (YYYY-MM-DD)"),
    limit: int = Query(50, description="Лимит результатов"),
    status: Optional[str] = Query(None, description="Статус звонка (active/completed/failed)")
):
    """Поиск трейсов звонков в БД"""
    try:
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Строим WHERE условие
            where_conditions = []
            params = []
            param_counter = 1
            
            if enterprise:
                where_conditions.append(f"enterprise_number = ${param_counter}")
                params.append(enterprise)
                param_counter += 1
            
            if phone:
                where_conditions.append(f"phone_number LIKE ${param_counter}")
                params.append(f"%{phone}%")
                param_counter += 1
            
            if date_from:
                where_conditions.append(f"start_time >= ${param_counter}::timestamp")
                params.append(date_from)
                param_counter += 1
            
            if date_to:
                where_conditions.append(f"start_time < ${param_counter}::timestamp + interval '1 day'")
                params.append(date_to)
                param_counter += 1
            
            if status:
                where_conditions.append(f"call_status = ${param_counter}")
                params.append(status)
                param_counter += 1
            
            # Строим SQL запрос
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            query = f"""
            SELECT 
                unique_id,
                enterprise_number,
                phone_number,
                call_status,
                start_time,
                end_time,
                CASE 
                    WHEN jsonb_typeof(call_events) = 'array' THEN jsonb_array_length(call_events)
                    ELSE 0 
                END as events_count,
                CASE 
                    WHEN jsonb_typeof(http_requests) = 'array' THEN jsonb_array_length(http_requests)
                    ELSE 0 
                END as http_count,
                CASE 
                    WHEN jsonb_typeof(sql_queries) = 'array' THEN jsonb_array_length(sql_queries)
                    ELSE 0 
                END as sql_count,
                CASE 
                    WHEN jsonb_typeof(telegram_messages) = 'array' THEN jsonb_array_length(telegram_messages)
                    ELSE 0 
                END as tg_count,
                CASE 
                    WHEN jsonb_typeof(integration_responses) = 'array' THEN jsonb_array_length(integration_responses)
                    ELSE 0 
                END as int_count,
                created_at,
                updated_at
            FROM call_traces
            WHERE {where_clause}
            ORDER BY start_time DESC
            LIMIT ${param_counter}
            """
            params.append(limit)
            
            results = await conn.fetch(query, *params)
        
        formatted_results = []
        for row in results:
            formatted_results.append({
                "unique_id": row['unique_id'],
                "enterprise_number": row['enterprise_number'],
                "phone_number": row['phone_number'],
                "call_status": row['call_status'],
                "start_time": row['start_time'].isoformat() if row['start_time'] else None,
                "end_time": row['end_time'].isoformat() if row['end_time'] else None,
                "events_count": row['events_count'],
                "http_requests_count": row['http_count'],
                "sql_queries_count": row['sql_count'],
                "telegram_messages_count": row['tg_count'],
                "integration_responses_count": row['int_count'],
                "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None
            })
        
        return {
            "results": formatted_results,
            "total": len(formatted_results),
            "limit": limit,
            "filters": {
                "enterprise": enterprise,
                "phone": phone,
                "date_from": date_from,
                "date_to": date_to,
                "status": status
            }
        }
        
    except Exception as e:
        logger.error(f"Error searching traces: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "service": "logger",
        "port": 8026,
        "database": "connected"
    }

# ═══════════════════════════════════════════════════════════════════
# API ENDPOINTS ДЛЯ УПРАВЛЕНИЯ ПАРТИЦИЯМИ
# ═══════════════════════════════════════════════════════════════════

@app.get("/partitions")
async def get_partitions():
    """Список всех партиций предприятий"""
    try:
        partitions = await list_enterprise_partitions()
        return {
            "status": "success",
            "partitions": partitions,
            "total": len(partitions)
        }
    except Exception as e:
        logger.error(f"Error getting partitions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/partitions/{enterprise_number}")
async def create_partition(enterprise_number: str):
    """Создание партиции для предприятия"""
    try:
        # Валидация номера предприятия
        if not enterprise_number.isdigit() or len(enterprise_number) != 4:
            raise HTTPException(status_code=400, detail="Enterprise number must be 4 digits")
        
        result = await create_enterprise_partition_api(enterprise_number)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating partition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/partitions/{enterprise_number}")
async def delete_partition(enterprise_number: str, force: bool = False):
    """Удаление партиции предприятия"""
    try:
        # Валидация номера предприятия
        if not enterprise_number.isdigit() or len(enterprise_number) != 4:
            raise HTTPException(status_code=400, detail="Enterprise number must be 4 digits")
        
        if force:
            # Принудительное удаление с данными
            # ОПТИМИЗАЦИЯ: Используем connection pool
            async with db_pool.acquire() as conn:
                partition_name = f"call_traces_{enterprise_number}"
                drop_query = f"DROP TABLE IF EXISTS {partition_name} CASCADE"
                await conn.execute(drop_query)
            
            logger.info(f"✅ Force dropped partition {partition_name}")
            return {"status": "success", "partition": partition_name, "forced": True}
        else:
            result = await drop_enterprise_partition_api(enterprise_number)
            
            if result["status"] == "error":
                raise HTTPException(status_code=400, detail=result["message"])
            
            return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting partition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/partitions/{enterprise_number}/stats")
async def get_partition_stats(enterprise_number: str):
    """Статистика по партиции предприятия"""
    try:
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            partition_name = f"call_traces_{enterprise_number}"
            
            # Проверяем существует ли партиция
            exists_query = """
            SELECT EXISTS (
                SELECT 1 FROM pg_tables 
                WHERE tablename = $1 AND schemaname = 'public'
            )
            """
            exists = await conn.fetchval(exists_query, partition_name)
            
            if not exists:
                raise HTTPException(status_code=404, detail=f"Partition for enterprise {enterprise_number} not found")
            
            # Получаем статистику
            stats_query = f"""
            SELECT 
                COUNT(*) as total_calls,
                COUNT(CASE WHEN call_status = 'completed' THEN 1 END) as completed_calls,
                COUNT(CASE WHEN call_status = 'failed' THEN 1 END) as failed_calls,
                AVG(CASE 
                    WHEN end_time IS NOT NULL AND start_time IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (end_time - start_time))
                    ELSE NULL 
                END) as avg_duration,
                MIN(start_time) as first_call,
                MAX(start_time) as last_call,
                pg_size_pretty(pg_total_relation_size('"{partition_name}"')) as partition_size
            FROM "{partition_name}"
            """
            
            stats = await conn.fetchrow(stats_query)
        
        return {
            "status": "success",
            "enterprise_number": enterprise_number,
            "partition_name": partition_name,
            "stats": {
                "total_calls": stats['total_calls'],
                "completed_calls": stats['completed_calls'],
                "failed_calls": stats['failed_calls'],
                "avg_duration_seconds": float(stats['avg_duration']) if stats['avg_duration'] else 0,
                "first_call": stats['first_call'].isoformat() if stats['first_call'] else None,
                "last_call": stats['last_call'].isoformat() if stats['last_call'] else None,
                "partition_size": stats['partition_size']
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting partition stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════════════════════════════
# ПРОСМОТР ДЕТАЛЬНОЙ ИНФОРМАЦИИ О ЗВОНКЕ
# ═══════════════════════════════════════════════════════════════════

@app.get("/call/{enterprise_number}/{unique_id}", response_class=HTMLResponse)
async def view_call_details(
    request: Request,
    enterprise_number: str,
    unique_id: str,
    token: str = Query(..., description="Secret токен для авторизации")
):
    """
    Просмотр детальной информации о звонке в красивом HTML формате
    
    Args:
        enterprise_number: Номер предприятия (например, 0367)
        unique_id: Уникальный ID звонка из Asterisk
        token: Secret токен для авторизации
    
    Returns:
        HTML страница с детальной информацией о звонке
    """
    import asyncpg
    
    try:
        # Проверяем права доступа
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            result = await conn.fetchrow(
                "SELECT secret, name FROM enterprises WHERE number = $1",
                enterprise_number
            )
            
            if not result or result['secret'] != token:
                raise HTTPException(status_code=403, detail="Access denied")
            
            enterprise_info = {"name": result['name'], "number": enterprise_number}
        
        # Получаем данные о звонке (партиция называется просто номером предприятия)
        partition_name = enterprise_number
        
        # ОПТИМИЗАЦИЯ: Используем connection pool
        async with db_pool.acquire() as conn:
            # Ищем запись по Asterisk UniqueId в JSONB массиве call_events
            query = f"""
                SELECT 
                    unique_id,
                    enterprise_number,
                    phone_number,
                    call_direction,
                    call_status,
                    start_time,
                    end_time,
                    call_events,
                    telegram_messages,
                    http_requests,
                    sql_queries,
                    integration_responses,
                    created_at,
                    updated_at
                FROM "{partition_name}"
                WHERE call_events @> $1::jsonb
                LIMIT 1
            """
            
            # Ищем событие с Asterisk UniqueId
            search_pattern = json.dumps([{"event_data": {"UniqueId": unique_id}}])
            call_data = await conn.fetchrow(query, search_pattern)
            
            if not call_data:
                raise HTTPException(status_code=404, detail="Call not found")
            
            # Преобразуем в dict
            call_data = dict(call_data)
            
            # Парсим JSONB поля если они строки
            if isinstance(call_data.get('call_events'), str):
                call_data['call_events'] = json.loads(call_data['call_events'])
            if isinstance(call_data.get('telegram_messages'), str):
                call_data['telegram_messages'] = json.loads(call_data['telegram_messages'])
            if isinstance(call_data.get('http_requests'), str):
                call_data['http_requests'] = json.loads(call_data['http_requests'])
            if isinstance(call_data.get('sql_queries'), str):
                call_data['sql_queries'] = json.loads(call_data['sql_queries'])
            if isinstance(call_data.get('integration_responses'), str):
                call_data['integration_responses'] = json.loads(call_data['integration_responses'])
            
            # Группируем события - убираем дубли по chat_id
            # Группируем по: тип события + UniqueId (для событий с UniqueId)
            unique_events = []
            seen_events = set()
            
            if call_data.get('call_events'):
                for event in call_data['call_events']:
                    event_data = event.get('event_data', {})
                    unique_id = event_data.get('UniqueId', '')
                    
                    # Для событий с UniqueId - группируем по типу + UniqueId
                    # Для событий без UniqueId (bridge_create, bridge_destroy) - только по типу
                    if unique_id:
                        event_key = (event.get('event_type'), unique_id)
                    else:
                        # Для событий без UniqueId берем первое вхождение
                        event_key = (event.get('event_type'), 'no_uid')
                    
                    if event_key not in seen_events:
                        seen_events.add(event_key)
                        unique_events.append(event)
                
                # Сортируем по времени
                unique_events.sort(key=lambda x: x.get('event_timestamp', ''))
                
                # Конвертируем timestamp строки в datetime для шаблона
                from datetime import datetime as dt
                from dateutil import parser
                for event in unique_events:
                    if event.get('event_timestamp'):
                        try:
                            # Парсим ISO формат с timezone
                            ts_str = event['event_timestamp']
                            event['event_timestamp_dt'] = parser.isoparse(ts_str)
                        except:
                            # Fallback - пробуем без timezone
                            try:
                                if '+' in ts_str:
                                    ts_str = ts_str.split('+')[0]
                                event['event_timestamp_dt'] = dt.fromisoformat(ts_str.replace('Z', ''))
                            except:
                                pass
                
                call_data['unique_events'] = unique_events
                logger.info(f"Total call_events: {len(call_data['call_events'])}, Unique events: {len(unique_events)}")
            
            # Извлекаем информацию об участниках из call_events
            client_info = {"name": "Не определен", "phone": ""}
            manager_info = {"name": "Не определен", "phone": "", "extension": ""}
            duration_seconds = 0
            call_direction = "unknown"
            call_start_time = None
            
            if call_data.get('call_events'):
                for event in call_data['call_events']:
                    event_type = event.get('event_type')
                    event_data = event.get('event_data', {})
                    
                    # Ищем информацию о клиенте и направлении из dial события
                    if event_type == 'dial' and event_data.get('Phone') and not client_info['phone']:
                        client_info['phone'] = event_data['Phone']
                        
                        # Определяем направление по CallType
                        call_type = event_data.get('CallType')
                        if call_type == 1:
                            call_direction = "outgoing"
                        elif call_type == 0:
                            call_direction = "incoming"
                        
                        # Берем время начала из dial события
                        if not call_start_time and event.get('event_timestamp'):
                            try:
                                from dateutil import parser
                                call_start_time = parser.isoparse(event['event_timestamp'])
                            except:
                                pass
                        
                        # Извлекаем внутренний номер менеджера из Extensions
                        extensions = event_data.get('Extensions', [])
                        if extensions and len(extensions) > 0 and extensions[0]:
                            manager_info['extension'] = extensions[0]
                            manager_info['phone'] = extensions[0]
                            manager_info['name'] = extensions[0]  # По умолчанию, будет перезаписано из enrichment
                    
                    # Ищем информацию о менеджере из bridge события
                    if event_type == 'bridge' and event_data.get('CallerIDNum'):
                        caller_id = event_data['CallerIDNum']
                        # Менеджер - это тот у кого короткий номер (внутренний)
                        if len(caller_id) <= 4 and caller_id.isdigit():
                            if not manager_info['extension']:  # Только если еще не установлено из dial
                                manager_info['extension'] = caller_id
                                manager_info['phone'] = caller_id
                                manager_info['name'] = event_data.get('CallerIDName', caller_id)
                    
                    # Рассчитываем длительность из события hangup
                    if event_type == 'hangup' and not duration_seconds:
                        start_time_str = event_data.get('StartTime')
                        end_time_str = event_data.get('EndTime')
                        
                        if start_time_str and end_time_str:
                            try:
                                from datetime import datetime as dt
                                start = dt.strptime(start_time_str, '%Y-%m-%d %H:%M:%S')
                                end = dt.strptime(end_time_str, '%Y-%m-%d %H:%M:%S')
                                duration_seconds = int((end - start).total_seconds())
                            except Exception as e:
                                logger.warning(f"Failed to calculate duration: {e}")
            
            # Обогащаем имена из HTTP запросов (metadata/enrich)
            if call_data.get('http_requests'):
                for req in call_data['http_requests']:
                    if 'metadata' in req.get('url', '') and 'enrich' in req.get('url', ''):
                        response_data = req.get('response_data', {})
                        if response_data:
                            # Имя клиента
                            if response_data.get('customer_name') and client_info['name'] == "Не определен":
                                client_info['name'] = response_data['customer_name']
                            # Имя менеджера (перезаписываем даже если было "151")
                            if response_data.get('manager_name'):
                                manager_info['name'] = response_data['manager_name']
                            if response_data.get('manager_personal_phone'):
                                manager_info['personal_phone'] = response_data['manager_personal_phone']
                            break
        
        # Функция для конвертации времени в GMT+3
        def to_gmt3(dt):
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone(timedelta(hours=3)))
        
        # Функция для форматирования телефона с префиксами
        def format_phone(phone):
            if not phone:
                return ""
            # Убираем все кроме цифр
            digits = ''.join(filter(str.isdigit, str(phone)))
            if len(digits) == 12 and digits.startswith('375'):
                # Формат: +375 (XX) XXX-XX-XX
                return f"+{digits[0:3]} ({digits[3:5]}) {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
            return phone
        
        # Рендерим HTML страницу
        return templates.TemplateResponse(
            "call_details.html",
            {
                "request": request,
                "call_data": call_data,
                "enterprise_info": enterprise_info,
                "enterprise_number": enterprise_number,
                "unique_id": unique_id,
                "client_info": client_info,
                "manager_info": manager_info,
                "duration_seconds": duration_seconds,
                "call_direction": call_direction,
                "call_start_time": call_start_time,
                "format_phone": format_phone,
                "to_gmt3": to_gmt3,
                "now": datetime.now
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error viewing call details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════════════════════════════
# ЗАПУСК СЕРВИСА
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting Call Logger Service on port 8026")
    uvicorn.run(
        "logger:app",
        host="0.0.0.0",
        port=8026,
        log_level="info"
    )
