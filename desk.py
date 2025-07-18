#!/usr/bin/env python3
"""
Desk Микросервис - сервис-заглушка для будущего функционала
Порт: 8011
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncpg

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Desk Service",
    description="Сервис-заглушка для будущего функционала",
    version="1.0.0"
)

# Монтируем статические файлы (фавиконы и другие ресурсы)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Модели данных
class StatusResponse(BaseModel):
    status: str
    message: str
    timestamp: str
    version: str

class HealthResponse(BaseModel):
    health: str
    uptime: str
    service: str

# Глобальные переменные
start_time = datetime.now()

# Конфигурация базы данных
DATABASE_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres', 
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

async def get_db_connection():
    """Получить соединение с базой данных"""
    try:
        conn = await asyncpg.connect(**DATABASE_CONFIG)
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        return None

async def get_latest_hangup_calls(enterprise_id: str, limit: int = 200) -> List[Dict]:
    """Получить последние hangup события для предприятия"""
    conn = await get_db_connection()
    if not conn:
        return []
    
    try:
        query = """
            SELECT 
                c.id,
                c.unique_id,
                c.phone_number,
                c.duration,
                c.call_status,
                c.call_type,
                c.start_time,
                c.end_time,
                c.timestamp,
                COALESCE(ce.raw_data, c.raw_data) as raw_data,
                ARRAY_AGG(cp.extension ORDER BY cp.ring_order) FILTER (WHERE cp.extension IS NOT NULL) as extensions,
                ARRAY_AGG(cp.participant_status ORDER BY cp.ring_order) FILTER (WHERE cp.participant_status IS NOT NULL) as statuses
            FROM calls c
            LEFT JOIN call_participants cp ON c.id = cp.call_id
            LEFT JOIN call_events ce ON c.unique_id = ce.unique_id AND ce.event_type = 'hangup'
            WHERE c.enterprise_id = $1 
            GROUP BY c.id, c.unique_id, c.phone_number, c.duration, c.call_status, c.call_type, c.start_time, c.end_time, c.timestamp, ce.raw_data, c.raw_data
            ORDER BY c.timestamp DESC
            LIMIT $2
        """
        
        rows = await conn.fetch(query, enterprise_id, limit)
        return [dict(row) for row in rows]
        
    except Exception as e:
        logger.error(f"Ошибка получения звонков для {enterprise_id}: {e}")
        return []
    finally:
        await conn.close()

async def get_extension_owners(enterprise_id: str) -> Dict[str, str]:
    """Получить владельцев внутренних номеров"""
    conn = await get_db_connection()
    if not conn:
        return {}
    
    try:
        query = """
            SELECT 
                uip.phone_number,
                TRIM(COALESCE(u.last_name, '') || ' ' || COALESCE(u.first_name, '')) AS full_name
            FROM user_internal_phones uip
            LEFT JOIN users u ON uip.user_id = u.id
            WHERE uip.enterprise_number = $1
        """
        
        rows = await conn.fetch(query, enterprise_id)
        return {row['phone_number']: row['full_name'] for row in rows if row['full_name'].strip()}
        
    except Exception as e:
        logger.error(f"Ошибка получения владельцев номеров для {enterprise_id}: {e}")
        return {}
    finally:
        await conn.close()

def format_phone_display(phone: str) -> str:
    """Форматирует номер телефона для отображения"""
    if not phone:
        return "Неизвестный"
    
    # Удаляем все не-цифровые символы
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) == 12 and digits.startswith('375'):
        # Белорусский номер: +375 (29) 123-45-67
        return f"+375 ({digits[3:5]}) {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif len(digits) == 11 and digits.startswith('7'):
        # Российский номер: +7 (999) 123-45-67
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    else:
        return phone

def format_duration(duration: int) -> str:
    """Форматирует длительность в MM:SS"""
    if duration <= 0:
        return "00:00"
    minutes = duration // 60
    seconds = duration % 60
    return f"{minutes:02d}:{seconds:02d}"

def format_call_time(call_time) -> str:
    """Форматирует время звонка с поправкой GMT +3"""
    if not call_time:
        return ""
    
    # Часовой пояс GMT+3
    gmt_plus_3 = timezone(timedelta(hours=3))
    
    # Если это строка, пробуем конвертировать в datetime
    if isinstance(call_time, str):
        try:
            call_time = datetime.fromisoformat(call_time.replace('Z', '+00:00'))
        except:
            return call_time[:16] if len(call_time) >= 16 else call_time
    
    if not isinstance(call_time, datetime):
        return str(call_time)
    
    # Если время без timezone info, считаем что это UTC
    if call_time.tzinfo is None:
        call_time = call_time.replace(tzinfo=timezone.utc)
    
    # Конвертируем в GMT+3
    call_time_local = call_time.astimezone(gmt_plus_3)
    
    # Текущее время в GMT+3
    now_local = datetime.now(gmt_plus_3)
    today = now_local.date()
    yesterday = today - timedelta(days=1)
    call_date = call_time_local.date()
    
    time_str = call_time_local.strftime("%H:%M")
    
    if call_date == today:
        return time_str
    elif call_date == yesterday:
        return f"Вчера {time_str}"
    else:
        return f"{call_time_local.strftime('%d.%m.%Y')} {time_str}"

@app.on_event("startup")
async def startup_event():
    """Событие запуска сервиса"""
    logger.info("🚀 Запуск Desk сервиса на порту 8011")
    logger.info("📋 Сервис-заглушка готов к работе")

@app.on_event("shutdown")
async def shutdown_event():
    """Событие остановки сервиса"""
    logger.info("🛑 Остановка Desk сервиса")

@app.get("/", response_class=HTMLResponse)
async def root(
    enterprise: str = Query(None, description="Название предприятия"),
    number: str = Query(None, description="Номер предприятия")
):
    """Корневой эндпоинт - возвращает HTML страницу рабочего стола"""
    enterprise_name = enterprise or "Предприятие"
    enterprise_number = number or "0000"
    
    # Формируем заголовок в формате "номер-название"
    full_title = f"{enterprise_number}-{enterprise_name}"
    
    # Получаем данные звонков и владельцев номеров
    calls_data = await get_latest_hangup_calls(enterprise_number, 200)
    extension_owners = await get_extension_owners(enterprise_number)
    
    # Формируем HTML для таблицы звонков
    calls_html = ""
    for call in calls_data:
        # Время звонка  
        start_time_raw = call.get('start_time')
        if start_time_raw is None:
            # Используем timestamp как fallback
            start_time_raw = call.get('timestamp')
        call_time = format_call_time(start_time_raw)
        
        # Длительность
        duration = format_duration(call.get('duration', 0))
        
        # Определение участников звонка
        raw_data = call.get('raw_data')
        if raw_data is None:
            raw_data = {}
        elif isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except:
                raw_data = {}
        elif not isinstance(raw_data, dict):
            raw_data = {}
        
        phone = call.get('phone_number', '')
        call_type = call.get('call_type', '0')
        
        # Получаем информацию о внутренних номерах из call_participants
        db_extensions = call.get('extensions', []) or []
        statuses = call.get('statuses', []) or []
        
        # Для обратной совместимости также проверяем raw_data
        raw_extensions = raw_data.get('Extensions', []) or []
        
        # Используем данные из БД если есть, иначе из raw_data
        extensions = db_extensions if db_extensions else raw_extensions
        
        # Форматирование участников
        if call_type == '2':  # Внутренний звонок
            caller = raw_data.get('CallerIDNum', '')
            callee = extensions[0] if extensions else ''
            
            caller_display = extension_owners.get(caller, caller) if caller else caller
            callee_display = extension_owners.get(callee, callee) if callee else callee
            
            if caller != caller_display and caller:
                caller_display = f"{caller_display} {caller}"
            if callee != callee_display and callee:
                callee_display = f"{callee_display} {callee}"
                
            participants = f"{caller_display} → {callee_display}"
        else:  # Внешний звонок
            formatted_phone = format_phone_display(phone)
            if extensions:
                ext = extensions[0]
                ext_display = extension_owners.get(ext, ext)
                if ext != ext_display and ext:
                    ext_display = f"{ext_display} {ext}"
                
                if call_type == '1':  # Исходящий
                    participants = f"{ext_display} → {formatted_phone}"
                else:  # Входящий (call_type == '0')
                    participants = f"{formatted_phone} → {ext_display}"
            else:
                # Если нет информации о внутреннем номере
                if call_type == '1':  # Исходящий
                    participants = f"??? → {formatted_phone}"
                elif call_type == '0':  # Входящий
                    participants = f"{formatted_phone} → ???"
                else:
                    participants = formatted_phone
        
        # Статус звонка (цвет строки)
        call_status = call.get('call_status', '0')
        row_class = "success" if call_status == '2' else "warning" if call_status == '0' else "default"
        
        calls_html += f"""
        <tr class="{row_class}">
            <td>{call_time}</td>
            <td><button class="btn btn-sm btn-secondary" disabled>🎵</button></td>
            <td>{duration}</td>
            <td>{participants}</td>
        </tr>
        """
    
    html_content = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>{full_title} Рабочий стол</title>
    
    <!-- Favicon and App Icons -->
    <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="96x96" href="/static/apple-touch-icon.png">
    <link rel="manifest" href="/static/site.webmanifest">
    <meta name="theme-color" content="#2563eb">
    <meta name="msapplication-TileColor" content="#2563eb">
    
    <style>
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; 
            background-color: #f8f9fa; 
            margin: 0; 
            padding: 0; 
        }}
        .header {{ 
            display: flex; 
            align-items: center; 
            background-color: #343a40; 
            color: white; 
            padding: 0.5rem 1rem; 
            border-bottom: 1px solid #ddd; 
        }}
        .header img {{ 
            height: 32px; 
            margin-right: 15px; 
        }}
        .header h1 {{ 
            font-size: 1.1rem; 
            margin: 0; 
            font-weight: 400; 
            color: rgba(255, 255, 255, 0.85); 
        }}
        .container {{ 
            padding: 2rem; 
        }}
        .btn {{ 
            background-color: #007bff; 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            text-align: center; 
            text-decoration: none; 
            display: inline-block; 
            font-size: 16px; 
            margin-right: 0.5rem; 
            margin-bottom: 1rem; 
            cursor: pointer; 
            border-radius: 5px; 
        }}
        .btn:hover {{ 
            background-color: #0056b3; 
        }}
        .btn-primary {{ 
            background-color: #007bff; 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            border-radius: 5px; 
            cursor: pointer; 
        }}
        .btn-secondary {{ 
            background-color: #6c757d; 
            padding: 10px 20px; 
        }}
        .btn-success {{ 
            background-color: #28a745; 
            padding: 10px 20px; 
        }}
        .btn-sm {{ 
            padding: 5px 10px; 
            font-size: 12px; 
            border-radius: 4px; 
            margin-bottom: 0; 
        }}
        .table-container {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            overflow: hidden;
            margin-top: 1rem;
        }}
        .table-container h3 {{
            margin: 0;
            padding: 1rem 1.5rem;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
            color: #495057;
            font-size: 1.1rem;
            font-weight: 600;
        }}
        .calls-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}
        .calls-table th {{
            background: #343a40;
            color: white;
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            border: none;
        }}
        .calls-table td {{
            padding: 10px 8px;
            border-bottom: 1px solid #dee2e6;
            vertical-align: middle;
        }}
        .calls-table tr.success {{
            background-color: #d4edda;
        }}
        .calls-table tr.warning {{
            background-color: #fff3cd;
        }}
        .calls-table tr.default {{
            background-color: #f8f9fa;
        }}
        .calls-table tr:hover {{
            background-color: #e9ecef;
        }}
        .calls-table tr.success:hover {{
            background-color: #c3e6cb;
        }}
        .calls-table tr.warning:hover {{
            background-color: #ffeaa7;
        }}
        
        @media (max-width: 768px) {{
            .header {{
                padding: 1rem;
            }}
            .container {{
                padding: 1rem;
            }}
            .calls-table {{
                font-size: 0.8rem;
            }}
            .calls-table th, .calls-table td {{
                padding: 8px 4px;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <img src="/static/logo.jpg" alt="Логотип">
        <h1>{full_title} Рабочий стол</h1>
    </div>
    
    <div class="container">
        <div class="table-container">
            <h3>История звонков (последние 200)</h3>
            <table class="calls-table">
                <thead>
                    <tr>
                        <th>Время</th>
                        <th>Запись</th>
                        <th>Длительность</th>
                        <th>Участники</th>
                    </tr>
                </thead>
                <tbody>
                    {calls_html}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
    """
    
    return html_content

@app.get("/api")
async def api_root():
    """API эндпоинт - возвращает JSON информацию о сервисе"""
    return {
        "service": "Desk Service",
        "status": "running",
        "message": "Сервис-заглушка работает",
        "version": "1.0.0",
        "port": 8011,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервиса"""
    uptime = datetime.now() - start_time
    return HealthResponse(
        health="healthy",
        uptime=str(uptime),
        service="desk-service"
    )

@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Получение статуса сервиса"""
    return StatusResponse(
        status="active",
        message="Desk сервис работает нормально",
        timestamp=datetime.now().isoformat(),
        version="1.0.0"
    )

@app.get("/info")
async def get_info():
    """Информация о сервисе"""
    return {
        "name": "Desk Service", 
        "description": "Сервис-заглушка для будущего функционала",
        "version": "1.0.0",
        "port": 8011,
        "endpoints": [
            "/",
            "/health",
            "/status", 
            "/info",
            "/ping"
        ],
        "started_at": start_time.isoformat(),
        "uptime_seconds": (datetime.now() - start_time).total_seconds()
    }

@app.get("/ping")
async def ping():
    """Простой ping для проверки доступности"""
    return {"ping": "pong", "timestamp": datetime.now().isoformat()}

@app.post("/test")
async def test_endpoint(request: Request):
    """Тестовый POST эндпоинт"""
    try:
        body = await request.json()
    except:
        body = {}
    
    return {
        "message": "Тестовый эндпоинт работает",
        "received_data": body,
        "timestamp": datetime.now().isoformat()
    }

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Обработчик 404 ошибок"""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Эндпоинт не найден",
            "path": request.url.path,
            "available_endpoints": ["/", "/health", "/status", "/info", "/ping", "/test"],
            "service": "desk-service"
        }
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    """Обработчик внутренних ошибок"""
    logger.error(f"Внутренняя ошибка: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Внутренняя ошибка сервера",
            "service": "desk-service",
            "timestamp": datetime.now().isoformat()
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8011) 