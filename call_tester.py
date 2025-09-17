#!/usr/bin/env python3
"""
Сервис тестирования звонков - call_tester.py
Порт 8025

Веб-интерфейс для эмуляции звонков предприятий с выбором:
- Типа звонка (1-1, 2-1, 3-1, etc.)
- Внешних номеров
- Менеджеров 
- Линий
- Результата звонка

Отправляет полную последовательность событий start→dial→bridge→hangup
в сервис 8000 и показывает результаты в реальном времени.
"""

import asyncio
import asyncpg
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Call Tester Service", description="Тестирование звонков для предприятий")

# Настройка шаблонов
templates = Jinja2Templates(directory="templates")

# Настройка статических файлов для фавиконов
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Конфигурация
TARGET_SERVICE_URL = "http://localhost:8000"

# Подключение к БД
DATABASE_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres', 
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

class CallTestService:
    """Основной класс сервиса тестирования"""
    
    def __init__(self):
        self.db_pool = None
        self.enterprises_cache = {}  # {enterprise_number: {managers: {}, lines: {}, tokens: {}}}
        
    async def init_db(self):
        """Инициализация пула подключений к БД"""
        try:
            self.db_pool = await asyncpg.create_pool(**DATABASE_CONFIG)
            logger.info("✅ Database pool created successfully")
            await self.load_enterprises_list()
        except Exception as e:
            logger.error(f"❌ Failed to create database pool: {e}")
            
    async def load_enterprises_list(self):
        """Загрузка списка всех предприятий"""
        try:
            async with self.db_pool.acquire() as conn:
                enterprises = await conn.fetch("""
                    SELECT number, name, secret
                    FROM enterprises
                    WHERE number IS NOT NULL AND number != ''
                    ORDER BY number
                """)
                
                for ent in enterprises:
                    self.enterprises_cache[ent['number']] = {
                        'name': ent['name'],
                        'token': ent['secret'],
                        'managers': {},
                        'lines': {},
                        'loaded': False
                    }
                
                logger.info(f"✅ Loaded {len(self.enterprises_cache)} enterprises")
                
        except Exception as e:
            logger.error(f"❌ Failed to load enterprises: {e}")
    
    async def load_enterprise_data(self, enterprise_number: str) -> bool:
        """Загрузка данных конкретного предприятия"""
        logger.info(f"🔍 Trying to load enterprise {enterprise_number}")
        
        if enterprise_number not in self.enterprises_cache:
            logger.error(f"❌ Enterprise {enterprise_number} not found in cache. Available: {list(self.enterprises_cache.keys())[:5]}...")
            return False
            
        if self.enterprises_cache[enterprise_number]['loaded']:
            logger.info(f"✅ Enterprise {enterprise_number} already loaded")
            return True  # Уже загружено
            
        try:
            async with self.db_pool.acquire() as conn:
                # Загружаем менеджеров
                managers = await conn.fetch("""
                    SELECT uip.phone_number as internal_phone, u.first_name, u.last_name, 
                           u.patronymic as middle_name, u.personal_phone
                    FROM user_internal_phones uip
                    JOIN users u ON uip.user_id = u.id
                    WHERE uip.enterprise_number = $1
                    ORDER BY uip.phone_number
                """, enterprise_number)
                
                managers_data = {}
                for mgr in managers:
                    full_name = " ".join(filter(None, [mgr['first_name'], mgr['last_name']]))
                    if not full_name.strip():
                        full_name = f"Менеджер {mgr['internal_phone']}"
                    
                    managers_data[mgr['internal_phone']] = {
                        'name': full_name,
                        'personal_phone': mgr['personal_phone'],
                        'follow_me_number': None,  # Пока убираем, так как нет в схеме
                        'follow_me_enabled': False
                    }
                
                # Загружаем линии GSM
                gsm_lines = await conn.fetch("""
                    SELECT gl.line_id, gl.internal_id, gl.phone_number, gl.line_name,
                           gl.prefix, g.name as goip_name, g.ip_address as goip_ip,
                           s.name as shop_name
                    FROM gsm_lines gl
                    LEFT JOIN goip g ON gl.goip_id = g.id
                    LEFT JOIN shop_lines sl ON gl.line_id = sl.line_id
                    LEFT JOIN shops s ON sl.shop_id = s.id
                    WHERE gl.enterprise_number = $1
                    ORDER BY gl.line_id
                """, enterprise_number)
                
                lines_data = {}
                for line in gsm_lines:
                    line_name = line['line_name'] or f"GSM-{line['line_id']}"
                    # Определяем оператора из названия
                    operator = "Unknown"
                    if any(op in line_name.upper() for op in ['A1', 'МТС', 'LIFE']):
                        if 'A1' in line_name.upper():
                            operator = "A1"
                        elif 'МТС' in line_name.upper():
                            operator = "МТС"
                        elif 'LIFE' in line_name.upper():
                            operator = "Life"
                    
                    lines_data[line['line_id']] = {
                        'name': line_name,
                        'phone': line['phone_number'],
                        'operator': operator,
                        'goip_name': line['goip_name'],
                        'shop_name': line['shop_name']
                    }
                
                # Сохраняем в кэше
                self.enterprises_cache[enterprise_number]['managers'] = managers_data
                self.enterprises_cache[enterprise_number]['lines'] = lines_data
                self.enterprises_cache[enterprise_number]['loaded'] = True
                
                logger.info(f"✅ Loaded {len(managers_data)} managers and {len(lines_data)} lines for {enterprise_number}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to load data for enterprise {enterprise_number}: {e}")
            return False
    
    async def send_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        """Отправка события в сервис 8000"""
        try:
            endpoint_map = {
                "start": "/start",
                "dial": "/dial",
                "bridge": "/bridge", 
                "hangup": "/hangup"
            }
            
            endpoint = endpoint_map.get(event_type)
            if not endpoint:
                logger.error(f"Unknown event type: {event_type}")
                return False
            
            url = f"{TARGET_SERVICE_URL}{endpoint}"
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=data)
                
                if response.status_code == 200:
                    logger.info(f"✅ Event {event_type} sent successfully")
                    return True
                else:
                    logger.error(f"❌ Event {event_type} failed: HTTP {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error sending {event_type}: {e}")
            return False
    
    async def simulate_call(self, call_params: Dict[str, Any]) -> Dict[str, Any]:
        """Эмуляция полного звонка"""
        try:
            # Генерируем уникальные данные
            unique_id = f"test_{int(datetime.now().timestamp())}.{uuid.uuid4().hex[:8]}"
            start_time = datetime.now()
            duration_seconds = call_params.get('duration_seconds', 180)  # 3 минуты по умолчанию
            end_time = start_time + timedelta(seconds=duration_seconds)
            
            call_type = call_params['call_type']
            external_phone = call_params['external_phone']
            internal_phone = call_params['internal_phone']
            line_id = call_params['line_id']
            call_status = call_params['call_status']  # 2=ответили, 3=не ответили
            enterprise_number = call_params['enterprise_number']
            enterprise_token = call_params['enterprise_token']
            
            # Результат эмуляции
            result = {
                'unique_id': unique_id,
                'events_sent': 0,
                'events_failed': 0,
                'errors': [],
                'success': False
            }
            
            # 1. START событие
            start_data = {
                "Event": "start",
                "UniqueId": unique_id,
                "Token": enterprise_token,
                "CallerIDNum": external_phone if call_type == 0 else internal_phone,
                "Extensions": [internal_phone],
                "CallType": call_type,
                "Exten": line_id,
                "StartTime": start_time.isoformat(),
                "Channel": f"SIP/{line_id}-00000001" if call_type == 0 else f"SIP/{internal_phone}-00000001"
            }
            
            if await self.send_event("start", start_data):
                result['events_sent'] += 1
            else:
                result['events_failed'] += 1
                result['errors'].append("Failed to send start event")
            
            await asyncio.sleep(2)  # Задержка между событиями
            
            # 2. DIAL событие
            dial_data = {
                "Event": "dial", 
                "UniqueId": unique_id,
                "Token": enterprise_token,
                "CallerIDNum": external_phone if call_type == 0 else internal_phone,
                "Phone": external_phone,
                "Extensions": [internal_phone],
                "CallType": call_type,
                "Trunk": line_id,
                "Channel": f"SIP/{line_id}-00000001" if call_type == 0 else f"SIP/{internal_phone}-00000001"
            }
            
            if await self.send_event("dial", dial_data):
                result['events_sent'] += 1
            else:
                result['events_failed'] += 1
                result['errors'].append("Failed to send dial event")
                
            await asyncio.sleep(3)
            
            # 3. BRIDGE событие (только если ответили)
            if call_status == 2:  # Ответили
                bridge_data = {
                    "Event": "bridge",
                    "UniqueId": unique_id,
                    "Token": enterprise_token,
                    "CallerIDNum": external_phone if call_type == 0 else internal_phone,
                    "ConnectedLineNum": internal_phone if call_type == 0 else external_phone,
                    "CallType": call_type,
                    "Channel": f"SIP/{internal_phone}-00000002",
                    "DestChannel": f"SIP/{line_id}-00000001"
                }
                
                if await self.send_event("bridge", bridge_data):
                    result['events_sent'] += 1
                else:
                    result['events_failed'] += 1
                    result['errors'].append("Failed to send bridge event")
                    
                await asyncio.sleep(duration_seconds)  # Разговор
            else:
                await asyncio.sleep(10)  # Короткая задержка если не ответили
            
            # 4. HANGUP событие
            hangup_data = {
                "Event": "hangup",
                "UniqueId": unique_id,
                "Token": enterprise_token,
                "CallerIDNum": external_phone if call_type == 0 else internal_phone,
                "Phone": external_phone,
                "Extensions": [internal_phone],
                "CallType": call_type,
                "CallStatus": call_status,
                "StartTime": start_time.isoformat(),
                "EndTime": end_time.isoformat(),
                "Trunk": line_id,
                "Channel": f"SIP/{internal_phone}-00000002" if call_status == 2 else f"SIP/{line_id}-00000001",
                "Cause": "16" if call_status == 2 else "19",
                "CauseTxt": "Normal Clearing" if call_status == 2 else "No Answer"
            }
            
            if await self.send_event("hangup", hangup_data):
                result['events_sent'] += 1
            else:
                result['events_failed'] += 1
                result['errors'].append("Failed to send hangup event")
            
            result['success'] = result['events_failed'] == 0
            return result
            
        except Exception as e:
            logger.error(f"❌ Call simulation failed: {e}")
            return {
                'unique_id': unique_id if 'unique_id' in locals() else 'unknown',
                'events_sent': 0,
                'events_failed': 1,
                'errors': [str(e)],
                'success': False
            }

# Глобальный экземпляр сервиса
test_service = CallTestService()

@app.on_event("startup")
async def startup():
    """Инициализация при запуске"""
    await test_service.init_db()

@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request, enterprise: str = "0367"):
    """Главная страница с интерфейсом тестирования"""
    
    # Проверяем что предприятие существует в кэше
    if enterprise not in test_service.enterprises_cache:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    
    # Временно загружаем данные асинхронно, не блокируя страницу
    # await test_service.load_enterprise_data(enterprise)
    
    enterprise_data = test_service.enterprises_cache[enterprise]
    
    # Типы звонков
    call_types = [
        {"id": 0, "name": "2-1 - Входящий (простой)", "description": "Внешний → Внутренний"},
        {"id": 1, "name": "1-1 - Исходящий (простой)", "description": "Внутренний → Внешний"},
        {"id": 2, "name": "3-1 - Внутренний", "description": "Внутренний → Внутренний"}
    ]
    
    # Статусы звонков
    call_statuses = [
        {"id": 2, "name": "Ответили", "icon": "✅"},
        {"id": 3, "name": "Не ответили", "icon": "❌"}
    ]
    
    return templates.TemplateResponse("test_interface.html", {
        "request": request,
        "enterprise_number": enterprise,
        "enterprise_name": enterprise_data['name'],
        "call_types": call_types,
        "call_statuses": call_statuses,
        "managers": enterprise_data['managers'],
        "lines": enterprise_data['lines']
    })

@app.post("/api/test-call")
async def test_call_api(
    call_type: int = Form(...),
    external_phone: str = Form(...),
    internal_phone: str = Form(...),
    line_id: str = Form(...),
    call_status: int = Form(...),
    duration_minutes: int = Form(3),
    enterprise: str = Form("0367")
):
    """API для запуска тестового звонка"""
    
    try:
        # Проверяем предприятие
        if enterprise not in test_service.enterprises_cache:
            raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
            
        enterprise_data = test_service.enterprises_cache[enterprise]
        
        # Валидация
        if not external_phone.strip():
            raise HTTPException(status_code=400, detail="Внешний номер обязателен")
        if not internal_phone.strip():
            raise HTTPException(status_code=400, detail="Внутренний номер обязателен")
        if not line_id.strip():
            raise HTTPException(status_code=400, detail="Линия обязательна")
            
        # Параметры звонка
        call_params = {
            'call_type': call_type,
            'external_phone': external_phone.strip(),
            'internal_phone': internal_phone.strip(),
            'line_id': line_id.strip(),
            'call_status': call_status,
            'duration_seconds': duration_minutes * 60,
            'enterprise_number': enterprise,
            'enterprise_token': enterprise_data['token']
        }
        
        # Запускаем эмуляцию
        result = await test_service.simulate_call(call_params)
        
        return JSONResponse({
            "success": result['success'],
            "message": "Тестовый звонок завершен",
            "details": {
                "unique_id": result['unique_id'],
                "events_sent": result['events_sent'],
                "events_failed": result['events_failed'],
                "errors": result['errors']
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Test call API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/managers")
async def get_managers(enterprise: str = "0367"):
    """API для получения списка менеджеров"""
    if not await test_service.load_enterprise_data(enterprise):
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    return JSONResponse(test_service.enterprises_cache[enterprise]['managers'])

@app.get("/api/lines") 
async def get_lines(enterprise: str = "0367"):
    """API для получения списка линий"""
    if not await test_service.load_enterprise_data(enterprise):
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    return JSONResponse(test_service.enterprises_cache[enterprise]['lines'])

@app.get("/api/enterprises")
async def get_enterprises():
    """API для получения списка предприятий"""
    enterprises = []
    for number, data in test_service.enterprises_cache.items():
        enterprises.append({
            'number': number,
            'name': data['name'],
            'has_token': bool(data['token'])
        })
    return JSONResponse(enterprises)

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    total_managers = sum(len(data['managers']) for data in test_service.enterprises_cache.values())
    total_lines = sum(len(data['lines']) for data in test_service.enterprises_cache.values())
    
    return {
        "status": "healthy",
        "enterprises_loaded": len(test_service.enterprises_cache),
        "total_managers": total_managers,
        "total_lines": total_lines,
        "database_connected": test_service.db_pool is not None
    }

if __name__ == "__main__":
    print("🧪 Starting Universal Call Test Service")
    print("📡 URL: http://localhost:8025") 
    print("🎯 Supports: All enterprises")
    print("🔗 Target: http://localhost:8000")
    print("📋 Usage: http://localhost:8025/?enterprise=XXXX")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8025,
        log_level="info"
    )
