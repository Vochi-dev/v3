#!/usr/bin/env python3
"""
Desk Микросервис - сервис-заглушка для будущего функционала
Порт: 8011
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
async def root(enterprise: str = Query(None, description="Название предприятия")):
    """Корневой эндпоинт - возвращает HTML страницу рабочего стола"""
    enterprise_name = enterprise or "Предприятие"
    
    html_content = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Рабочий стол {enterprise_name}</title>
    
    <!-- Favicon and App Icons -->
    <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <link rel="icon" type="image/png" sizes="192x192" href="/static/android-chrome-192x192.png">
    <link rel="icon" type="image/png" sizes="512x512" href="/static/android-chrome-512x512.png">
    <link rel="manifest" href="/static/site.webmanifest">
    
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        
        .header {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 1rem 2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }}
        
        .header h1 {{
            color: white;
            margin: 0;
            font-size: 1.5rem;
            font-weight: 600;
        }}
        
        .header .info {{
            color: rgba(255, 255, 255, 0.8);
            font-size: 0.9rem;
        }}
        
        .container {{
            flex: 1;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        
        .welcome-card {{
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            padding: 3rem;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
            max-width: 600px;
            width: 100%;
        }}
        
        .welcome-card h2 {{
            color: #333;
            margin: 0 0 1rem 0;
            font-size: 2rem;
            font-weight: 700;
        }}
        
        .welcome-card p {{
            color: #666;
            font-size: 1.1rem;
            line-height: 1.6;
            margin-bottom: 2rem;
        }}
        
        .features-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.5rem;
            margin-top: 2rem;
        }}
        
        .feature-card {{
            background: rgba(255, 255, 255, 0.9);
            border-radius: 15px;
            padding: 1.5rem;
            text-align: center;
            box-shadow: 0 10px 20px rgba(0, 0, 0, 0.05);
            transition: transform 0.3s ease;
        }}
        
        .feature-card:hover {{
            transform: translateY(-5px);
        }}
        
        .feature-icon {{
            font-size: 3rem;
            margin-bottom: 1rem;
        }}
        
        .feature-title {{
            font-size: 1.2rem;
            font-weight: 600;
            color: #333;
            margin-bottom: 0.5rem;
        }}
        
        .feature-desc {{
            color: #666;
            font-size: 0.9rem;
        }}
        
        .status-info {{
            background: rgba(40, 167, 69, 0.1);
            border: 1px solid rgba(40, 167, 69, 0.3);
            border-radius: 10px;
            padding: 1rem;
            margin-top: 2rem;
            text-align: center;
            color: #155724;
        }}
        
        .footer {{
            text-align: center;
            padding: 1rem;
            color: rgba(255, 255, 255, 0.7);
            font-size: 0.9rem;
        }}
        
        @media (max-width: 768px) {{
            .header {{
                padding: 1rem;
            }}
            .container {{
                padding: 1rem;
            }}
            .welcome-card {{
                padding: 2rem;
            }}
            .features-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🖥️ Рабочий стол {enterprise_name}</h1>
        <div class="info">
            Порт: 8011 | Сервис: Desk
        </div>
    </div>
    
    <div class="container">
        <div class="welcome-card">
            <h2>Добро пожаловать!</h2>
            <p>Это рабочий стол для предприятия <strong>{enterprise_name}</strong>. 
            Здесь будут размещены инструменты и функции для работы с системой.</p>
            
            <div class="status-info">
                ✅ Сервис работает нормально | Версия 1.0.0 | Время запуска: {start_time.strftime('%d.%m.%Y %H:%M:%S')}
            </div>
            
            <div class="features-grid">
                <div class="feature-card">
                    <div class="feature-icon">📊</div>
                    <div class="feature-title">Аналитика</div>
                    <div class="feature-desc">Статистика и отчеты по работе системы</div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-icon">⚙️</div>
                    <div class="feature-title">Настройки</div>
                    <div class="feature-desc">Конфигурация и параметры системы</div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-icon">📞</div>
                    <div class="feature-title">Телефония</div>
                    <div class="feature-desc">Управление звонками и линиями</div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-icon">👥</div>
                    <div class="feature-title">Пользователи</div>
                    <div class="feature-desc">Управление пользователями и правами</div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>&copy; 2025 Asterisk Webhook System | Desk Service v1.0.0</p>
    </div>
    
    <script>
        // Логирование для отладки
        console.log('🖥️ Рабочий стол {enterprise_name} загружен');
        console.log('⏰ Время загрузки:', new Date().toLocaleString());
        
        // Простая анимация при загрузке
        document.addEventListener('DOMContentLoaded', function() {{
            const cards = document.querySelectorAll('.feature-card');
            cards.forEach((card, index) => {{
                setTimeout(() => {{
                    card.style.opacity = '0';
                    card.style.transform = 'translateY(20px)';
                    card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
                    
                    setTimeout(() => {{
                        card.style.opacity = '1';
                        card.style.transform = 'translateY(0)';
                    }}, 50);
                }}, index * 100);
            }});
        }});
    </script>
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