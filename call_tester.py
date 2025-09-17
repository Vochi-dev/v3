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
                # Загружаем все внутренние телефоны (с менеджерами и без)
                managers = await conn.fetch("""
                    SELECT uip.phone_number as internal_phone, 
                           u.first_name, u.last_name, u.patronymic as middle_name, 
                           u.personal_phone
                    FROM user_internal_phones uip
                    LEFT JOIN users u ON uip.user_id = u.id
                    WHERE uip.enterprise_number = $1
                    ORDER BY CASE WHEN uip.phone_number ~ '^[0-9]+$' 
                                  THEN uip.phone_number::int 
                                  ELSE 9999 END, uip.phone_number
                """, enterprise_number)
                
                managers_data = {}
                for mgr in managers:
                    # Если есть привязка к менеджеру - показываем фамилию, иначе просто номер
                    if mgr['first_name'] or mgr['last_name']:
                        full_name = " ".join(filter(None, [mgr['first_name'], mgr['last_name']]))
                        display_name = f"{mgr['internal_phone']} - {full_name}"
                    else:
                        display_name = mgr['internal_phone']
                    
                    managers_data[mgr['internal_phone']] = {
                        'name': display_name,
                        'personal_phone': mgr['personal_phone'] or '',
                        'follow_me_number': None,
                        'follow_me_enabled': False
                    }
                
                # Загружаем GSM линии (БЕЗ shop_lines!)
                gsm_lines = await conn.fetch("""
                    SELECT gl.line_id, gl.internal_id, gl.phone_number, gl.line_name,
                           gl.prefix, g.gateway_name as goip_name, g.device_ip as goip_ip
                    FROM gsm_lines gl
                    LEFT JOIN goip g ON gl.goip_id = g.id
                    WHERE gl.enterprise_number = $1
                    ORDER BY gl.line_id
                """, enterprise_number)
                
                # Загружаем SIP линии  
                sip_lines = await conn.fetch("""
                    SELECT su.id as line_id, su.line_name, su.line_name as phone_number,
                           su.prefix, sp.name as provider_name, 'SIP' as line_type
                    FROM sip_unit su
                    LEFT JOIN sip sp ON su.provider_id = sp.id
                    WHERE su.enterprise_number = $1
                    ORDER BY su.id
                """, enterprise_number)
                
                lines_data = {}
                
                # Обрабатываем GSM линии
                for line in gsm_lines:
                    line_name = line['line_name'] or f"GSM-{line['line_id']}"
                    # Определяем оператора из названия
                    operator = "GSM"
                    if any(op in line_name.upper() for op in ['A1', 'МТС', 'LIFE']):
                        if 'A1' in line_name.upper():
                            operator = "A1"
                        elif 'МТС' in line_name.upper():
                            operator = "МТС"
                        elif 'LIFE' in line_name.upper():
                            operator = "Life"
                    
                    lines_data[line['line_id']] = {
                        'name': f"{line_name} (GSM)",
                        'phone': line['phone_number'] or '',
                        'operator': operator,
                        'type': 'GSM',
                        'goip_name': line['goip_name']
                    }
                
                # Обрабатываем SIP линии
                for line in sip_lines:
                    line_name = line['line_name'] or f"SIP-{line['line_id']}"
                    
                    lines_data[f"SIP-{line['line_id']}"] = {
                        'name': f"{line_name} (SIP)",
                        'phone': line['phone_number'] or '',
                        'operator': 'SIP',
                        'type': 'SIP',
                        'provider_name': line['provider_name'],
                        'prefix': line['prefix']
                    }
                
                # Сохраняем в кэше
                self.enterprises_cache[enterprise_number]['managers'] = managers_data
                self.enterprises_cache[enterprise_number]['lines'] = lines_data
                self.enterprises_cache[enterprise_number]['loaded'] = True
                
                logger.info(f"✅ Loaded {len(managers_data)} managers and {len(lines_data)} lines for {enterprise_number}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to load data for enterprise {enterprise_number}: {e}")
            import traceback
            logger.error(f"❌ Traceback: {traceback.format_exc()}")
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
    
    # Загружаем РЕАЛЬНЫЕ данные из БД
    await test_service.load_enterprise_data(enterprise)
    
    enterprise_data = test_service.enterprises_cache[enterprise]
    
    # Типы звонков - все 45 типов из Типы_звонков_v2.txt
    call_types = [
        # Исходящие (1-X)
        {"id": 1, "name": "1-1 - Исходящий простой", "description": "Внутренний → Внешний (ответили)"},
        {"id": 2, "name": "1-2 - Исходящий простой", "description": "Внутренний → Внешний (не ответили)"},
        {"id": 3, "name": "1-3 - Исходящий переключен", "description": "Внутренний → Внешний → Переключение"},
        {"id": 4, "name": "1-4 - Исходящий трехстороний", "description": "Внутренний → Внешний → Еще один"},
        {"id": 5, "name": "1-5 - Исходящий трехстороний-2", "description": "Трехсторонний с разрывом"},
        {"id": 6, "name": "1-6 - Исходящий многоуровневый", "description": "Множественные переключения"},
        {"id": 7, "name": "1-7 - Исходящий с возвратом", "description": "Переключение с возвратом"},
        {"id": 8, "name": "1-8 - Исходящий кольцевой", "description": "Кольцевые переключения"},
        {"id": 9, "name": "1-9 - Исходящий с отбоем", "description": "Отбой во время переключения"},
        {"id": 10, "name": "1-10 - Исходящий с парковкой", "description": "Парковка вызова"},
        {"id": 11, "name": "1-11 - Исходящий групповой", "description": "Групповой вызов"},
        
        # Входящие (2-X)
        {"id": 12, "name": "2-1 - Входящий простой", "description": "Внешний → Внутренний (ответили)"},
        {"id": 13, "name": "2-2 - Входящий простой", "description": "Внешний → Внутренний (не ответили)"},
        {"id": 14, "name": "2-3 - Входящий на группу", "description": "Внешний → Группа"},
        {"id": 15, "name": "2-4 - Входящий с IVR", "description": "Внешний → IVR → Внутренний"},
        {"id": 16, "name": "2-5 - Входящий с очередью", "description": "Внешний → Очередь → Внутренний"},
        {"id": 17, "name": "2-6 - Входящий переключен", "description": "Внешний → Внутренний → Переключение"},
        {"id": 18, "name": "2-7 - Входящий трехстороний", "description": "Внешний → Внутренний → Еще один"},
        {"id": 19, "name": "2-8 - Входящий с Follow-Me", "description": "Follow-Me на мобильный"},
        {"id": 20, "name": "2-9 - Входящий с записью", "description": "Входящий с записью разговора"},
        {"id": 21, "name": "2-10 - Входящий экстренный", "description": "Экстренное переключение"},
        {"id": 22, "name": "2-11 - Входящий конференция", "description": "Конференц-связь"},
        {"id": 23, "name": "2-12 - Входящий голосовая почта", "description": "Переход на голосовую почту"},
        {"id": 24, "name": "2-13 - Входящий множественный", "description": "Множественные операторы"},
        {"id": 25, "name": "2-14 - Входящий временный", "description": "Временная маршрутизация"},
        {"id": 26, "name": "2-15 - Входящий приоритетный", "description": "Приоритетная обработка"},
        {"id": 27, "name": "2-16 - Входящий автоответчик", "description": "Автоматический ответчик"},
        {"id": 28, "name": "2-17 - Входящий с фильтром", "description": "Фильтрация звонков"},
        {"id": 29, "name": "2-18 - Входящий многоканальный", "description": "Многоканальная обработка"},
        {"id": 30, "name": "2-19 - Входящий с уведомлением", "description": "SMS/Email уведомления"},
        {"id": 31, "name": "2-20 - Входящий региональный", "description": "Региональная маршрутизация"},
        {"id": 32, "name": "2-21 - Входящий с аналитикой", "description": "Расширенная аналитика"},
        {"id": 33, "name": "2-22 - Входящий с коллбэком", "description": "Callback функция"},
        {"id": 34, "name": "2-23 - Входящий VIP", "description": "VIP обслуживание"},
        {"id": 35, "name": "2-24 - Входящий круглосуточный", "description": "24/7 поддержка"},
        {"id": 36, "name": "2-25 - Входящий с эскалацией", "description": "Эскалация вызовов"},
        {"id": 37, "name": "2-26 - Входящий многоязычный", "description": "Многоязычная поддержка"},
        {"id": 38, "name": "2-27 - Входящий с CRM", "description": "Интеграция с CRM"},
        {"id": 39, "name": "2-28 - Входящий сезонный", "description": "Сезонная маршрутизация"},
        {"id": 40, "name": "2-29 - Входящий с ботом", "description": "Интеграция с чат-ботом"},
        {"id": 41, "name": "2-30 - Входящий омниканальный", "description": "Омниканальная связь"},
        
        # Внутренние (3-X)
        {"id": 42, "name": "3-1 - Внутренний простой", "description": "Внутренний → Внутренний (ответили)"},
        {"id": 43, "name": "3-2 - Внутренний простой", "description": "Внутренний → Внутренний (не ответили)"},
        {"id": 44, "name": "3-3 - Внутренний переключен", "description": "Внутренний → Внутренний → Переключение"},
        {"id": 45, "name": "3-4 - Внутренний конференция", "description": "Внутренняя конференция"}
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
        "managers": enterprise_data.get('managers', {}),
        "lines": enterprise_data.get('lines', {})
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
    if enterprise not in test_service.enterprises_cache:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    
    # Загружаем РЕАЛЬНЫЕ данные из БД
    if not await test_service.load_enterprise_data(enterprise):
        raise HTTPException(status_code=500, detail="Ошибка загрузки данных менеджеров")
    
    return JSONResponse(test_service.enterprises_cache[enterprise].get('managers', {}))

@app.get("/api/lines") 
async def get_lines(enterprise: str = "0367"):
    """API для получения списка линий"""
    if enterprise not in test_service.enterprises_cache:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    
    # Загружаем РЕАЛЬНЫЕ данные из БД
    if not await test_service.load_enterprise_data(enterprise):
        raise HTTPException(status_code=500, detail="Ошибка загрузки данных линий")
    
    return JSONResponse(test_service.enterprises_cache[enterprise].get('lines', {}))

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
