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
# ВРЕМЕННОЕ ХРАНИЛИЩЕ (ЗАГЛУШКА)
# ═══════════════════════════════════════════════════════════════════

# В будущем это будет заменено на PostgreSQL
call_traces = {}  # unique_id -> call_trace_data

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
            
        unique_id = event.unique_id
        
        # Инициализируем трейс если его нет
        if unique_id not in call_traces:
            call_traces[unique_id] = {
                "enterprise_number": event.enterprise_number,
                "unique_id": unique_id,
                "events": [],
                "http_requests": [],
                "sql_queries": [],
                "telegram_messages": [],
                "created_at": event.timestamp,
                "updated_at": event.timestamp
            }
        
        # Добавляем событие
        call_traces[unique_id]["events"].append({
            "event_type": event.event_type,
            "event_data": event.event_data,
            "timestamp": event.timestamp.isoformat()
        })
        call_traces[unique_id]["updated_at"] = event.timestamp
        
        logger.info(f"Logged event {event.event_type} for call {unique_id}")
        return {"status": "success", "message": "Event logged"}
        
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
        if unique_id not in call_traces:
            raise HTTPException(status_code=404, detail="Call trace not found")
        
        trace = call_traces[unique_id]
        
        # Сортируем события по времени
        all_events = []
        
        # Добавляем события звонков
        for event in trace["events"]:
            all_events.append({
                "type": "call_event",
                "timestamp": event["timestamp"],
                "data": event
            })
        
        # Добавляем HTTP запросы
        for request in trace["http_requests"]:
            all_events.append({
                "type": "http_request",
                "timestamp": request["timestamp"],
                "data": request
            })
        
        # Добавляем SQL запросы
        for query in trace["sql_queries"]:
            all_events.append({
                "type": "sql_query",
                "timestamp": query["timestamp"],
                "data": query
            })
        
        # Добавляем Telegram сообщения
        for message in trace["telegram_messages"]:
            all_events.append({
                "type": "telegram_message",
                "timestamp": message["timestamp"],
                "data": message
            })
        
        # Сортируем по времени
        all_events.sort(key=lambda x: x["timestamp"])
        
        return {
            "unique_id": unique_id,
            "enterprise_number": trace["enterprise_number"],
            "created_at": trace["created_at"].isoformat(),
            "updated_at": trace["updated_at"].isoformat(),
            "timeline": all_events,
            "summary": {
                "total_events": len(trace["events"]),
                "http_requests": len(trace["http_requests"]),
                "sql_queries": len(trace["sql_queries"]),
                "telegram_messages": len(trace["telegram_messages"])
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
