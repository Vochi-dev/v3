"""
Сервис логирования звонков - logger.py
Порт: 8026

Централизованный сервис для логирования всех событий звонков
в структурированном виде с возможностью быстрого поиска и анализа.
"""

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import uvicorn
from datetime import datetime
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

# Временное хранилище (заглушка) - будет заменено на PostgreSQL
call_traces = {}  # unique_id -> call_trace_data

# ═══════════════════════════════════════════════════════════════════
# ФУНКЦИИ УПРАВЛЕНИЯ ПАРТИЦИЯМИ
# ═══════════════════════════════════════════════════════════════════

async def ensure_enterprise_partition(enterprise_number: str):
    """Автоматически создает партицию для предприятия если её нет"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        
        # Проверяем существует ли партиция (новая схема - простые названия)
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
        
        await conn.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Error ensuring partition for enterprise {enterprise_number}: {e}")
        return False

async def create_enterprise_partition_api(enterprise_number: str):
    """API функция для создания партиции предприятия"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        
        partition_name = f"call_traces_{enterprise_number}"
        remainder_val = int(enterprise_number)
        
        create_query = f"""
        CREATE TABLE {partition_name} PARTITION OF call_traces 
        FOR VALUES WITH (MODULUS 1000, REMAINDER {remainder_val})
        """
        
        await conn.execute(create_query)
        await conn.close()
        
        logger.info(f"✅ Created partition {partition_name}")
        return {"status": "success", "partition": partition_name}
        
    except Exception as e:
        logger.error(f"❌ Error creating partition: {e}")
        return {"status": "error", "message": str(e)}

async def drop_enterprise_partition_api(enterprise_number: str):
    """API функция для удаления партиции предприятия"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        
        partition_name = f"call_traces_{enterprise_number}"
        
        # Сначала проверяем есть ли данные
        count_query = f"SELECT COUNT(*) FROM {partition_name}"
        count = await conn.fetchval(count_query)
        
        if count > 0:
            await conn.close()
            return {"status": "error", "message": f"Partition contains {count} records. Use force=true to delete anyway."}
        
        # Удаляем партицию
        drop_query = f"DROP TABLE {partition_name}"
        await conn.execute(drop_query)
        await conn.close()
        
        logger.info(f"✅ Dropped partition {partition_name}")
        return {"status": "success", "partition": partition_name}
        
    except Exception as e:
        logger.error(f"❌ Error dropping partition: {e}")
        return {"status": "error", "message": str(e)}

async def list_enterprise_partitions():
    """Список всех партиций предприятий"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        
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
        await conn.close()
        
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
        
        # Сохраняем событие в БД
        conn = await asyncpg.connect(**DB_CONFIG)
        
        # Извлекаем номер телефона из данных события если есть
        phone_number = None
        if 'Phone' in event.event_data:
            phone_number = event.event_data['Phone']
        elif 'phone' in event.event_data:
            phone_number = event.event_data['phone']
        
        # Добавляем chat_id в event_data для сохранения в JSONB
        event_data_with_chat = event.event_data.copy()
        if event.chat_id:
            event_data_with_chat["_chat_id"] = event.chat_id
        
        # Добавляем событие через функцию БД
        trace_id = await conn.fetchval(
            "SELECT add_call_event($1, $2, $3, $4, $5)",
            event.unique_id,
            event.enterprise_number, 
            event.event_type,
            json.dumps(event_data_with_chat),
            phone_number
        )
        
        await conn.close()
        
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
            
        unique_id = request.unique_id
        
        if unique_id not in call_traces:
            call_traces[unique_id] = {
                "enterprise_number": request.enterprise_number,
                "unique_id": unique_id,
                "events": [],
                "http_requests": [],
                "sql_queries": [],
                "telegram_messages": [],
                "created_at": request.timestamp,
                "updated_at": request.timestamp
            }
        
        call_traces[unique_id]["http_requests"].append({
            "method": request.method,
            "url": request.url,
            "request_data": request.request_data,
            "response_data": request.response_data,
            "status_code": request.status_code,
            "duration_ms": request.duration_ms,
            "timestamp": request.timestamp.isoformat()
        })
        call_traces[unique_id]["updated_at"] = request.timestamp
        
        logger.info(f"Logged HTTP {request.method} {request.url} for call {unique_id}")
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
        
        if unique_id not in call_traces:
            call_traces[unique_id] = {
                "enterprise_number": query.enterprise_number,
                "unique_id": unique_id,
                "events": [],
                "http_requests": [],
                "sql_queries": [],
                "telegram_messages": [],
                "created_at": query.timestamp,
                "updated_at": query.timestamp
            }
        
        call_traces[unique_id]["sql_queries"].append({
            "query": query.query,
            "parameters": query.parameters,
            "result": query.result,
            "duration_ms": query.duration_ms,
            "timestamp": query.timestamp.isoformat()
        })
        call_traces[unique_id]["updated_at"] = query.timestamp
        
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
        
        if unique_id not in call_traces:
            call_traces[unique_id] = {
                "enterprise_number": message.enterprise_number,
                "unique_id": unique_id,
                "events": [],
                "http_requests": [],
                "sql_queries": [],
                "telegram_messages": [],
                "created_at": message.timestamp,
                "updated_at": message.timestamp
            }
        
        call_traces[unique_id]["telegram_messages"].append({
            "chat_id": message.chat_id,
            "message_type": message.message_type,
            "message_id": message.message_id,
            "message_text": message.message_text,
            "action": message.action,
            "timestamp": message.timestamp.isoformat()
        })
        call_traces[unique_id]["updated_at"] = message.timestamp
        
        logger.info(f"Logged Telegram {message.action} for call {unique_id}")
        return {"status": "success", "message": "Telegram message logged"}
        
    except Exception as e:
        logger.error(f"Error logging Telegram message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/trace/{unique_id}")
async def get_call_trace(unique_id: str):
    """Получение полного трейса звонка"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        
        # Получаем основную информацию о трейсе
        trace_info = await conn.fetchrow("""
            SELECT id, unique_id, enterprise_number, phone_number, call_direction, 
                   call_status, start_time, end_time, call_events,
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
            await conn.close()
            raise HTTPException(status_code=404, detail="Call trace not found")
        
        await conn.close()
        
        # Формируем timeline из JSONB поля call_events
        timeline = []
        if trace_info['call_events']:
            import json
            
            # Парсим JSONB в Python объект
            try:
                if isinstance(trace_info['call_events'], str):
                    events_list = json.loads(trace_info['call_events'])
                else:
                    events_list = trace_info['call_events']
                
                if isinstance(events_list, list):
                    for event in events_list:
                        timeline.append({
                            "sequence": event.get('event_sequence', 0),
                            "event_type": event.get('event_type', 'unknown'),
                            "timestamp": event.get('event_timestamp', ''),
                            "data": event.get('event_data', {})
                        })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse call_events: {e}")
        
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
                "total_events": len(timeline),
                "events_in_jsonb": len(trace_info['call_events']) if trace_info['call_events'] else 0
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
    limit: int = Query(50, description="Лимит результатов")
):
    """Поиск трейсов звонков"""
    try:
        results = []
        
        for unique_id, trace in call_traces.items():
            # Фильтр по предприятию
            if enterprise and trace["enterprise_number"] != enterprise:
                continue
            
            # Фильтр по номеру телефона (ищем в событиях)
            if phone:
                phone_found = False
                for event in trace["events"]:
                    if phone in str(event.get("event_data", {})):
                        phone_found = True
                        break
                if not phone_found:
                    continue
            
            # Фильтры по дате пока пропускаем (заглушка)
            
            results.append({
                "unique_id": unique_id,
                "enterprise_number": trace["enterprise_number"],
                "created_at": trace["created_at"].isoformat(),
                "updated_at": trace["updated_at"].isoformat(),
                "events_count": len(trace["events"]),
                "http_requests_count": len(trace["http_requests"]),
                "sql_queries_count": len(trace["sql_queries"]),
                "telegram_messages_count": len(trace["telegram_messages"])
            })
            
            if len(results) >= limit:
                break
        
        return {
            "results": results,
            "total": len(results),
            "limit": limit
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
        "traces_count": len(call_traces)
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
            conn = await asyncpg.connect(**DB_CONFIG)
            partition_name = f"call_traces_{enterprise_number}"
            drop_query = f"DROP TABLE IF EXISTS {partition_name} CASCADE"
            await conn.execute(drop_query)
            await conn.close()
            
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
        conn = await asyncpg.connect(**DB_CONFIG)
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
            await conn.close()
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
        await conn.close()
        
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
