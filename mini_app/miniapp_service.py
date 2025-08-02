#!/usr/bin/env python3
"""
Telegram Mini App Service
Сервис для обработки запросов от Telegram Mini App
"""

import asyncio
import logging
import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import uvicorn

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Telegram Mini App Service",
    description="API для Telegram Mini App Vochi CRM",
    version="1.0.0"
)

# CORS для работы с Telegram
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://web.telegram.org", "https://bot.vochi.by"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory="/root/asterisk-webhook/mini_app"), name="static")

# Конфигурация БД
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC МОДЕЛИ
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramUserRequest(BaseModel):
    """Базовый запрос с Telegram ID"""
    tg_id: int

class CallsRequest(TelegramUserRequest):
    """Запрос списка звонков"""
    filter_type: str = "all"  # all, incoming, outgoing
    filter_date: Optional[str] = None
    limit: int = 50

class ClientSearchRequest(TelegramUserRequest):
    """Запрос поиска клиентов"""
    query: str

class StatsRequest(TelegramUserRequest):
    """Запрос статистики"""
    pass

# ═══════════════════════════════════════════════════════════════════════════════
# ОСНОВНЫЕ МАРШРУТЫ
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def serve_mini_app():
    """Главная страница Mini App"""
    return FileResponse("/root/asterisk-webhook/mini_app/index.html")

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {"status": "ok", "service": "miniapp", "timestamp": datetime.now().isoformat()}

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/enterprise-info")
async def get_enterprise_info(request: TelegramUserRequest):
    """Получить информацию о предприятии пользователя"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        try:
            # Ищем пользователя по telegram_tg_id
            user_data = await conn.fetchrow("""
                SELECT u.id, u.email, u.enterprise_number, u.first_name, u.last_name,
                       e.name as enterprise_name, e.number as enterprise_number_full
                FROM users u
                JOIN enterprises e ON u.enterprise_number = e.number
                WHERE u.telegram_tg_id = $1 AND u.telegram_authorized = true
                LIMIT 1
            """, request.tg_id)
            
            if not user_data:
                raise HTTPException(status_code=404, detail="Пользователь не найден или не авторизован")
            
            return {
                "success": True,
                "user": {
                    "id": user_data['id'],
                    "email": user_data['email'],
                    "first_name": user_data['first_name'],
                    "last_name": user_data['last_name']
                },
                "enterprise": {
                    "number": user_data['enterprise_number'],
                    "name": user_data['enterprise_name']
                }
            }
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Ошибка получения информации о предприятии: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера")

@app.post("/calls")
async def get_calls(request: CallsRequest):
    """Получить список звонков"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        try:
            # Сначала получаем enterprise_number пользователя
            user_data = await conn.fetchrow("""
                SELECT enterprise_number FROM users 
                WHERE telegram_tg_id = $1 AND telegram_authorized = true
            """, request.tg_id)
            
            if not user_data:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            
            enterprise_number = user_data['enterprise_number']
            
            # Строим условия фильтрации
            where_conditions = ["token = $1"]
            params = [enterprise_number]
            param_count = 1
            
            if request.filter_type != "all":
                param_count += 1
                if request.filter_type == "incoming":
                    where_conditions.append(f"call_type = ${param_count}")
                    params.append(0)  # 0 = входящий
                elif request.filter_type == "outgoing":
                    where_conditions.append(f"call_type = ${param_count}")
                    params.append(1)  # 1 = исходящий
            
            if request.filter_date:
                param_count += 1
                where_conditions.append(f"DATE(start_time) = ${param_count}")
                params.append(request.filter_date)
            
            where_clause = " AND ".join(where_conditions)
            
            # Запрос звонков
            query = f"""
                SELECT 
                    id,
                    unique_id,
                    phone,
                    call_type,
                    call_status,
                    start_time,
                    end_time,
                    EXTRACT(EPOCH FROM (end_time - start_time))::int as duration,
                    extension,
                    shared_uuid_token
                FROM calls 
                WHERE {where_clause}
                ORDER BY start_time DESC 
                LIMIT $1
            """
            
            # Добавляем лимит как последний параметр
            params.append(request.limit)
            
            calls_data = await conn.fetch(query, *params)
            
            # Форматируем данные
            calls = []
            for call in calls_data:
                recording_url = None
                if call['shared_uuid_token']:
                    recording_url = f"https://bot.vochi.by/recordings/file/{call['shared_uuid_token']}"
                
                calls.append({
                    "id": call['id'],
                    "unique_id": call['unique_id'],
                    "phone": call['phone'],
                    "call_type": call['call_type'],
                    "call_status": call['call_status'],
                    "start_time": call['start_time'].isoformat() if call['start_time'] else None,
                    "end_time": call['end_time'].isoformat() if call['end_time'] else None,
                    "duration": call['duration'] or 0,
                    "extension": call['extension'],
                    "recording_url": recording_url
                })
            
            return {"success": True, "calls": calls}
            
        finally:
            await conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения звонков: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера")

@app.post("/clients/search")
async def search_clients(request: ClientSearchRequest):
    """Поиск клиентов"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        try:
            # Получаем enterprise_number пользователя
            user_data = await conn.fetchrow("""
                SELECT enterprise_number FROM users 
                WHERE telegram_tg_id = $1 AND telegram_authorized = true
            """, request.tg_id)
            
            if not user_data:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            
            enterprise_number = user_data['enterprise_number']
            query = f"%{request.query}%"
            
            # Поиск по номеру телефона или имени клиента
            clients_data = await conn.fetch("""
                SELECT 
                    phone,
                    COUNT(*) as calls_count,
                    MAX(start_time) as last_call,
                    SUM(EXTRACT(EPOCH FROM (end_time - start_time))::int) as total_duration
                FROM calls 
                WHERE token = $1 
                AND (phone LIKE $2 OR phone LIKE $3)
                GROUP BY phone
                ORDER BY last_call DESC
                LIMIT 20
            """, enterprise_number, query, request.query.replace('+', ''))
            
            # Форматируем данные
            clients = []
            for client in clients_data:
                clients.append({
                    "phone": client['phone'],
                    "name": None,  # TODO: интеграция с базой клиентов
                    "calls_count": client['calls_count'],
                    "last_call": client['last_call'].isoformat() if client['last_call'] else None,
                    "total_duration": client['total_duration'] or 0
                })
            
            return {"success": True, "clients": clients}
            
        finally:
            await conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка поиска клиентов: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера")

@app.post("/stats")
async def get_stats(request: StatsRequest):
    """Получить статистику"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        try:
            # Получаем enterprise_number пользователя
            user_data = await conn.fetchrow("""
                SELECT enterprise_number FROM users 
                WHERE telegram_tg_id = $1 AND telegram_authorized = true
            """, request.tg_id)
            
            if not user_data:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            
            enterprise_number = user_data['enterprise_number']
            
            # Статистика за последние 30 дней
            thirty_days_ago = datetime.now() - timedelta(days=30)
            
            stats_data = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_calls,
                    COUNT(CASE WHEN call_status = 2 THEN 1 END) as successful_calls,
                    AVG(EXTRACT(EPOCH FROM (end_time - start_time))::int) as avg_duration
                FROM calls 
                WHERE token = $1 
                AND start_time >= $2
            """, enterprise_number, thirty_days_ago)
            
            # Форматируем среднюю длительность
            avg_duration = stats_data['avg_duration'] or 0
            avg_duration_formatted = f"{int(avg_duration // 60)}:{int(avg_duration % 60):02d}"
            
            return {
                "success": True,
                "total_calls": stats_data['total_calls'] or 0,
                "successful_calls": stats_data['successful_calls'] or 0,
                "avg_duration": avg_duration_formatted
            }
            
        finally:
            await conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера")

# ═══════════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Добавляем заголовки безопасности"""
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Обработчик 404 ошибок"""
    return {"error": "Not Found", "detail": "Страница не найдена", "status_code": 404}

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    """Обработчик внутренних ошибок"""
    logger.error(f"Internal server error: {exc}")
    return {"error": "Internal Server Error", "detail": "Внутренняя ошибка сервера", "status_code": 500}

# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК СЕРВИСА
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting Telegram Mini App Service on port 8017...")
    uvicorn.run(
        "miniapp_service:app",
        host="0.0.0.0",
        port=8017,
        reload=False
    )