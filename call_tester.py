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
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Настройка логирования
import os
from datetime import datetime

# Создаем директорию для логов если её нет
os.makedirs("logs", exist_ok=True)

# Настройка логирования с записью в файл и консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('logs/call_tester_events.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Call Tester Service", description="Тестирование звонков для предприятий")

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем все домены
    allow_credentials=True,
    allow_methods=["*"],  # Разрешаем все методы
    allow_headers=["*"],  # Разрешаем все заголовки
)

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
                        # Для мобильных менеджеров - только ФИО без внутреннего номера
                        clean_name = full_name
                    else:
                        display_name = mgr['internal_phone']
                        clean_name = mgr['internal_phone']
                    
                    managers_data[mgr['internal_phone']] = {
                        'name': display_name,
                        'clean_name': clean_name,  # Имя без внутреннего номера
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
                    # Используем line_name как идентификатор (это номер SIP транка)
                    sip_line_name = line['line_name'] or f"SIP-{line['line_id']}"
                    provider_display = f" - {line['provider_name']}" if line['provider_name'] else ""
                    
                    lines_data[sip_line_name] = {
                        'name': f"{sip_line_name}{provider_display} (SIP)",
                        'phone': line['phone_number'] or '',
                        'operator': 'SIP',
                        'type': 'SIP',
                        'provider_name': line['provider_name'],
                        'prefix': line['prefix'],
                        'sip_trunk_id': sip_line_name  # Добавляем явно номер транка
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
            
            # Проверяем тип звонка для выбора паттерна
            if call_type == 2:  # Паттерн 1-2 (id=2 в интерфейсе)
                return await self.simulate_call_1_2(call_params, result, unique_id, start_time, end_time, duration_seconds)
            elif call_type == 1:  # Паттерн 1-1 (id=1 в интерфейсе)
                return await self.simulate_call_1_1(call_params, result, unique_id, start_time, end_time, duration_seconds)
            else:
                # Для остальных паттернов пока используем 1-1
                return await self.simulate_call_1_1(call_params, result, unique_id, start_time, end_time, duration_seconds)
            
        except Exception as e:
            logger.error(f"❌ Call simulation failed: {e}")
            return {
                'unique_id': unique_id if 'unique_id' in locals() else 'unknown',
                'events_sent': 0,
                'events_failed': 1,
                'errors': [str(e)],
                'success': False
            }
    
    async def simulate_call_1_1(self, call_params: Dict[str, Any], result: Dict[str, Any], 
                                unique_id: str, start_time: datetime, end_time: datetime, 
                                duration_seconds: int) -> Dict[str, Any]:
        """Эмуляция паттерна 1-1 (простой исходящий звонок) - 9 событий"""
        
        call_type = call_params['call_type']
        external_phone = call_params['external_phone']
        internal_phone = call_params['internal_phone']
        line_id = call_params['line_id']
        call_status = call_params['call_status']
        enterprise_token = call_params['enterprise_token']
        
        # 1. DIAL событие
        dial_data = {
            "Phone": external_phone,
            "ExtTrunk": "",
            "ExtPhone": "",
            "Extensions": [internal_phone],
            "UniqueId": unique_id,
            "Token": enterprise_token,
            "Trunk": line_id,
            "CallType": call_type
        }
        
        if await self.send_event("dial", dial_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send dial event")
            
        await asyncio.sleep(2)
        
        # 2. NEW_CALLERID событие
        new_callerid_data = {
            "CallerIDNum": external_phone,
            "Channel": f"SIP/{line_id}-55353474",
            "CallerIDName": "",
            "BridgeUniqueid": f"{uuid.uuid4()}",
            "UniqueId": f"{unique_id}.1",
            "ConnectedLineNum": internal_phone,
            "Token": enterprise_token,
            "ConnectedLineName": internal_phone,
            "Exten": external_phone,
            "Context": "from-out-office"
        }
        
        if await self.send_event("new_callerid", new_callerid_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid event")
            
        await asyncio.sleep(2)
        
        # 3. BRIDGE_CREATE событие
        bridge_id = str(uuid.uuid4())
        bridge_create_data = {
            "BridgeType": "",
            "BridgeUniqueid": bridge_id,
            "UniqueId": "",
            "BridgeCreator": "",
            "Token": enterprise_token,
            "BridgeName": "",
            "BridgeNumChannels": "0",
            "BridgeTechnology": "simple_bridge"
        }
        
        if await self.send_event("bridge_create", bridge_create_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_create event")
            
        await asyncio.sleep(2)
        
        # 4. BRIDGE событие (внешний канал)
        bridge_external_data = {
            "CallerIDNum": external_phone,
            "Channel": f"SIP/{line_id}-55353474",
            "CallerIDName": "",
            "BridgeUniqueid": bridge_id,
            "UniqueId": f"{unique_id}.1",
            "Exten": "",
            "ConnectedLineNum": internal_phone,
            "Token": enterprise_token,
            "ConnectedLineName": internal_phone
        }
        
        if await self.send_event("bridge", bridge_external_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge external event")
            
        await asyncio.sleep(2)
        
        # 5. BRIDGE событие (внутренний канал)
        bridge_internal_data = {
            "CallerIDNum": internal_phone,
            "Channel": f"SIP/{internal_phone}-31291763",
            "CallerIDName": internal_phone,
            "BridgeUniqueid": bridge_id,
            "UniqueId": unique_id,
            "Exten": external_phone,
            "ConnectedLineNum": "",
            "Token": enterprise_token,
            "ConnectedLineName": ""
        }
        
        if await self.send_event("bridge", bridge_internal_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge internal event")
            
        await asyncio.sleep(duration_seconds if call_status == 2 else 10)
        
        # 6. BRIDGE_LEAVE событие (внешний канал)
        bridge_leave_external_data = {
            "CallerIDNum": external_phone,
            "Channel": f"SIP/{line_id}-55353474",
            "CallerIDName": "",
            "BridgeUniqueid": bridge_id,
            "UniqueId": f"{unique_id}.1",
            "ConnectedLineNum": internal_phone,
            "Token": enterprise_token,
            "ConnectedLineName": internal_phone,
            "BridgeNumChannels": "1"
        }
        
        if await self.send_event("bridge_leave", bridge_leave_external_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_leave external event")
            
        await asyncio.sleep(1)
        
        # 7. BRIDGE_LEAVE событие (внутренний канал)
        bridge_leave_internal_data = {
            "CallerIDNum": internal_phone,
            "Channel": f"SIP/{internal_phone}-31291763",
            "CallerIDName": internal_phone,
            "BridgeUniqueid": bridge_id,
            "UniqueId": unique_id,
            "ConnectedLineNum": "",
            "Token": enterprise_token,
            "ConnectedLineName": "",
            "BridgeNumChannels": "0"
        }
        
        if await self.send_event("bridge_leave", bridge_leave_internal_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_leave internal event")
            
        await asyncio.sleep(1)
        
        # 8. BRIDGE_DESTROY событие
        bridge_destroy_data = {
            "BridgeType": "",
            "BridgeUniqueid": bridge_id,
            "UniqueId": "",
            "BridgeCreator": "",
            "Token": enterprise_token,
            "BridgeName": "",
            "BridgeNumChannels": "0",
            "BridgeTechnology": "simple_bridge"
        }
        
        if await self.send_event("bridge_destroy", bridge_destroy_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_destroy event")
            
        await asyncio.sleep(1)
        
        # 9. HANGUP событие
        hangup_data = {
            "Phone": external_phone,
            "StartTime": start_time.strftime('%Y-%m-%d %H:%M:%S'),
            "Extensions": [internal_phone],
            "UniqueId": unique_id,
            "DateReceived": start_time.strftime('%Y-%m-%d %H:%M:%S'),
            "Token": enterprise_token,
            "EndTime": end_time.strftime('%Y-%m-%d %H:%M:%S'),
            "CallType": call_type,
            "CallStatus": str(call_status),
            "Trunk": line_id
        }
        
        if await self.send_event("hangup", hangup_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send hangup event")
        
        result['success'] = result['events_failed'] == 0
        return result
    
    async def simulate_call_1_2(self, call_params: Dict[str, Any], result: Dict[str, Any], 
                                unique_id: str, start_time: datetime, end_time: datetime, 
                                duration_seconds: int) -> Dict[str, Any]:
        """Эмуляция паттерна 1-2 (инициация из сторонней системы) - 27 событий"""
        
        call_type = call_params['call_type']
        external_phone = call_params['external_phone']
        internal_phone = call_params['internal_phone']
        line_id = call_params['line_id']
        call_status = call_params['call_status']
        enterprise_token = call_params['enterprise_token']
        
        # Генерируем уникальные ID каналов как в реальном звонке
        unique_id_377 = f"{unique_id}.377"  # Основной канал
        unique_id_376 = f"{unique_id}.376"  # Локальный канал 1
        unique_id_379 = f"{unique_id}.379"  # Локальный канал 2
        unique_id_380 = f"{unique_id}.380"  # Главный канал звонка (dial/hangup)
        unique_id_381 = f"{unique_id}.381"  # Внешний SIP канал
        
        # Генерируем уникальные ID мостов
        bridge_id_1 = str(uuid.uuid4())  # Первый мост
        bridge_id_2 = str(uuid.uuid4())  # Второй мост  
        bridge_id_3 = str(uuid.uuid4())  # Третий мост
        
        # 1. NEW_CALLERID событие (UniqueId: 377)
        new_callerid_1_data = {
            "Exten": internal_phone,
            "ConnectedLineNum": "<unknown>",
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_377,
            "Channel": f"Local/{internal_phone}@web-originate-00000017;2",
            "Context": "set-extcall-callerid",
            "CallerIDNum": external_phone
        }
        
        if await self.send_event("new_callerid", new_callerid_1_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid 1 event")
        
        await asyncio.sleep(1)
        
        # 2. NEW_CALLERID событие с CallerIDName (UniqueId: 377)
        new_callerid_2_data = {
            "Exten": internal_phone,
            "ConnectedLineNum": "<unknown>",
            "CallerIDName": "Тестовый..покупатель..(Первое..Контактное..лицо)",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_377,
            "Channel": f"Local/{internal_phone}@web-originate-00000017;2",
            "Context": "set-extcall-callerid",
            "CallerIDNum": external_phone
        }
        
        if await self.send_event("new_callerid", new_callerid_2_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid 2 event")
        
        await asyncio.sleep(2)
        
        # 3. NEW_CALLERID событие (UniqueId: 376)
        new_callerid_3_data = {
            "Exten": internal_phone,
            "ConnectedLineNum": "<unknown>",
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_376,
            "Channel": f"Local/{internal_phone}@web-originate-00000017;1",
            "Context": "web-originate",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("new_callerid", new_callerid_3_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid 3 event")
        
        await asyncio.sleep(2)
        
        # 4. BRIDGE_CREATE событие (Мост 1)
        bridge_create_1_data = {
            "BridgeUniqueid": bridge_id_1,
            "BridgeType": "",
            "BridgeName": "<unknown>",
            "BridgeTechnology": "simple_bridge",
            "Token": enterprise_token,
            "UniqueId": "",
            "BridgeCreator": "<unknown>",
            "BridgeNumChannels": "0"
        }
        
        if await self.send_event("bridge_create", bridge_create_1_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_create 1 event")
        
        await asyncio.sleep(1)
        
        # 5. BRIDGE событие (UniqueId: 377)
        bridge_1_data = {
            "BridgeUniqueid": bridge_id_1,
            "Exten": internal_phone,
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "Тестовый..покупатель..(Первое..Контактное..лицо)",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_377,
            "Channel": f"Local/{internal_phone}@web-originate-00000017;2",
            "CallerIDNum": external_phone
        }
        
        if await self.send_event("bridge", bridge_1_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge 1 event")
        
        await asyncio.sleep(1)
        
        # 6. NEW_CALLERID событие (UniqueId: 379)
        new_callerid_4_data = {
            "Exten": internal_phone,
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_379,
            "Channel": f"Local/{external_phone}@inoffice-00000018;1",
            "Context": "inoffice",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("new_callerid", new_callerid_4_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid 4 event")
        
        await asyncio.sleep(1)
        
        # 7. BRIDGE событие (UniqueId: 378)
        unique_id_378 = f"{unique_id}.378"
        bridge_2_data = {
            "BridgeUniqueid": bridge_id_1,
            "Exten": "",
            "ConnectedLineNum": external_phone,
            "CallerIDName": "",
            "ConnectedLineName": "Тестовый..покупатель..(Первое..Контактное..лицо)",
            "Token": enterprise_token,
            "UniqueId": unique_id_378,
            "Channel": f"SIP/{internal_phone}-000000a4",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("bridge", bridge_2_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge 2 event")
        
        await asyncio.sleep(1)
        
        # 8. NEW_CALLERID событие (UniqueId: 380)
        new_callerid_5_data = {
            "Exten": external_phone,
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_380,
            "Channel": f"Local/{external_phone}@inoffice-00000018;2",
            "Context": "inoffice",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("new_callerid", new_callerid_5_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid 5 event")
        
        await asyncio.sleep(1)
        
        # 9. DIAL событие (ГЛАВНОЕ - UniqueId: 380)
        dial_data = {
            "Phone": external_phone.replace('+', ''),
            "Trunk": line_id,
            "Extensions": [internal_phone],
            "ExtPhone": "",
            "Token": enterprise_token,
            "UniqueId": unique_id_380,
            "CallType": call_type,
            "ExtTrunk": ""
        }
        
        if await self.send_event("dial", dial_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send dial event")
        
        await asyncio.sleep(1)
        
        # 10. NEW_CALLERID событие (UniqueId: 381)
        new_callerid_6_data = {
            "Exten": external_phone.replace('+', ''),
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_381,
            "Channel": f"SIP/{line_id}-000000a5",
            "Context": "from-out-office",
            "CallerIDNum": external_phone.replace('+', '')
        }
        
        if await self.send_event("new_callerid", new_callerid_6_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send new_callerid 6 event")
        
        await asyncio.sleep(7)  # Время до ответа
        
        # 11. BRIDGE_CREATE событие (Мост 2)
        bridge_create_2_data = {
            "BridgeUniqueid": bridge_id_2,
            "BridgeType": "",
            "BridgeName": "<unknown>",
            "BridgeTechnology": "simple_bridge",
            "Token": enterprise_token,
            "UniqueId": "",
            "BridgeCreator": "<unknown>",
            "BridgeNumChannels": "0"
        }
        
        if await self.send_event("bridge_create", bridge_create_2_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_create 2 event")
        
        await asyncio.sleep(1)
        
        # 12. BRIDGE_CREATE событие (Мост 3)
        bridge_create_3_data = {
            "BridgeUniqueid": bridge_id_3,
            "BridgeType": "",
            "BridgeName": "<unknown>",
            "BridgeTechnology": "simple_bridge",
            "Token": enterprise_token,
            "UniqueId": "",
            "BridgeCreator": "<unknown>",
            "BridgeNumChannels": "0"
        }
        
        if await self.send_event("bridge_create", bridge_create_3_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_create 3 event")
        
        await asyncio.sleep(1)
        
        # 13. BRIDGE событие (UniqueId: 381)
        bridge_3_data = {
            "BridgeUniqueid": bridge_id_2,
            "Exten": "",
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_381,
            "Channel": f"SIP/{line_id}-000000a5",
            "CallerIDNum": external_phone.replace('+', '')
        }
        
        if await self.send_event("bridge", bridge_3_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge 3 event")
        
        await asyncio.sleep(1)
        
        # 14. BRIDGE событие (UniqueId: 376)
        bridge_4_data = {
            "BridgeUniqueid": bridge_id_3,
            "Exten": internal_phone,
            "ConnectedLineNum": "<unknown>",
            "CallerIDName": "",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_376,
            "Channel": f"Local/{internal_phone}@web-originate-00000017;1",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("bridge", bridge_4_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge 4 event")
        
        await asyncio.sleep(1)
        
        # 15. BRIDGE событие (UniqueId: 380)
        bridge_5_data = {
            "BridgeUniqueid": bridge_id_2,
            "Exten": external_phone.replace('+', ''),
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_380,
            "Channel": f"Local/{external_phone}@inoffice-00000018;2",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("bridge", bridge_5_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge 5 event")
        
        await asyncio.sleep(1)
        
        # 16. BRIDGE событие (UniqueId: 379)
        bridge_6_data = {
            "BridgeUniqueid": bridge_id_3,
            "Exten": "",
            "ConnectedLineNum": internal_phone,
            "CallerIDName": "",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_379,
            "Channel": f"Local/{external_phone}@inoffice-00000018;1",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("bridge", bridge_6_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge 6 event")
        
        await asyncio.sleep(duration_seconds if call_status == 2 else 5)
        
        # 17. BRIDGE_LEAVE событие (UniqueId: 381)
        bridge_leave_1_data = {
            "BridgeUniqueid": bridge_id_2,
            "ConnectedLineNum": internal_phone,
            "BridgeNumChannels": "1",
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_381,
            "Channel": f"SIP/{line_id}-000000a5",
            "CallerIDNum": external_phone.replace('+', '')
        }
        
        if await self.send_event("bridge_leave", bridge_leave_1_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_leave 1 event")
        
        await asyncio.sleep(1)
        
        # 18. BRIDGE_LEAVE событие (UniqueId: 380)
        bridge_leave_2_data = {
            "BridgeUniqueid": bridge_id_2,
            "ConnectedLineNum": internal_phone,
            "BridgeNumChannels": "0",
            "CallerIDName": "<unknown>",
            "ConnectedLineName": "<unknown>",
            "Token": enterprise_token,
            "UniqueId": unique_id_380,
            "Channel": f"Local/{external_phone}@inoffice-00000018;2",
            "CallerIDNum": internal_phone
        }
        
        if await self.send_event("bridge_leave", bridge_leave_2_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_leave 2 event")
        
        await asyncio.sleep(1)
        
        # 19. BRIDGE_DESTROY событие (Мост 2)
        bridge_destroy_1_data = {
            "BridgeUniqueid": bridge_id_2,
            "BridgeType": "",
            "BridgeName": "<unknown>",
            "BridgeTechnology": "simple_bridge",
            "Token": enterprise_token,
            "UniqueId": "",
            "BridgeCreator": "<unknown>",
            "BridgeNumChannels": "0"
        }
        
        if await self.send_event("bridge_destroy", bridge_destroy_1_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send bridge_destroy 1 event")
        
        await asyncio.sleep(3)
        
        # 20. HANGUP событие (ГЛАВНОЕ - UniqueId: 380)
        hangup_data = {
            "EndTime": end_time.strftime('%Y-%m-%d %H:%M:%S'),
            "Trunk": line_id,
            "Phone": external_phone.replace('+', ''),
            "DateReceived": (start_time + timedelta(seconds=4)).strftime('%Y-%m-%d %H:%M:%S'),
            "Extensions": [internal_phone],
            "Token": enterprise_token,
            "UniqueId": unique_id_380,
            "CallStatus": str(call_status),
            "CallType": call_type,
            "StartTime": (start_time + timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')
        }
        
        if await self.send_event("hangup", hangup_data):
            result['events_sent'] += 1
        else:
            result['events_failed'] += 1
            result['errors'].append("Failed to send hangup event")
        
        await asyncio.sleep(1)
        
        # 21-27. Остальные BRIDGE_LEAVE и BRIDGE_DESTROY события
        remaining_events = [
            ("bridge_leave", unique_id_379, bridge_id_3),
            ("bridge_leave", unique_id_376, bridge_id_3),
            ("bridge_destroy", "", bridge_id_3),
            ("bridge_leave", unique_id_377, bridge_id_1),
            ("bridge_leave", unique_id_378, bridge_id_1),
            ("bridge_destroy", "", bridge_id_1),
            ("hangup", unique_id_377, "")
        ]
        
        for i, (event_type, uid, bridge_id) in enumerate(remaining_events, 21):
            if event_type == "bridge_leave":
                event_data = {
                    "BridgeUniqueid": bridge_id,
                    "ConnectedLineNum": internal_phone if i % 2 == 1 else external_phone,
                    "BridgeNumChannels": "1" if i % 2 == 1 else "0",
                    "CallerIDName": "<unknown>",
                    "ConnectedLineName": "<unknown>",
                    "Token": enterprise_token,
                    "UniqueId": uid,
                    "Channel": f"Local/{internal_phone}@web-originate-00000017;{1 if i % 2 == 0 else 2}" if "377" in uid or "376" in uid else f"SIP/{internal_phone}-000000a4",
                    "CallerIDNum": internal_phone
                }
            elif event_type == "bridge_destroy":
                event_data = {
                    "BridgeUniqueid": bridge_id,
                    "BridgeType": "",
                    "BridgeName": "<unknown>",
                    "BridgeTechnology": "simple_bridge",
                    "Token": enterprise_token,
                    "UniqueId": "",
                    "BridgeCreator": "<unknown>",
                    "BridgeNumChannels": "0"
                }
            else:  # hangup для 377
                event_data = {
                    "EndTime": (end_time - timedelta(seconds=15)).strftime('%Y-%m-%d %H:%M:%S'),
                    "Phone": external_phone,
                    "DateReceived": start_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "Extensions": [internal_phone],
                    "Token": enterprise_token,
                    "UniqueId": uid,
                    "CallStatus": str(call_status),
                    "CallType": call_type,
                    "StartTime": ""
                }
            
            if await self.send_event(event_type, event_data):
                result['events_sent'] += 1
            else:
                result['events_failed'] += 1
                result['errors'].append(f"Failed to send {event_type} {i} event")
            
            await asyncio.sleep(1)
        
        result['success'] = result['events_failed'] == 0
        return result

# Глобальный экземпляр сервиса
test_service = CallTestService()

@app.on_event("startup")
async def startup():
    """Инициализация при запуске"""
    await test_service.init_db()

@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request, enterprise: str):
    """Главная страница с интерфейсом тестирования"""
    
    # Проверяем что предприятие существует в кэше
    if enterprise not in test_service.enterprises_cache:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    
    # Загружаем РЕАЛЬНЫЕ данные из БД
    await test_service.load_enterprise_data(enterprise)
    
    enterprise_data = test_service.enterprises_cache[enterprise]
    
    # Типы звонков с описаниями из файла (убираем + спереди)
    call_types = [
        # Исходящие (1-X)
        {"id": 1, "name": "1-1", "description": "Прямой набор номера - абонент ответил"},
        {"id": 2, "name": "1-2", "description": "Набор номера с внешней инициацией (из сторонней программы, CRM) - звонок отвечен\n(сначала дается команда астеру на инициацию вызова, подтверждение менеджером, набор номера)"},
        {"id": 3, "name": "1-3", "description": "Переадресация вызова с запросом - вызов принят другим оператором - другой оператор поднял трубку, но не захотел разговаривать\nс этим абонентом, и положил трубку, разговор вернулся к первому оператору (151)"},
        {"id": 4, "name": "1-4", "description": "Переадресация вызова с запросом - вызов принят другим оператором - он согласился поговорить, 151-й положил трубку, и внешний абонент\nпоговорил со 152-м оператором"},
        {"id": 5, "name": "1-5", "description": "Переадресация вызова с запросом - вызов не принят другим оператором (нет на месте)"},
        {"id": 6, "name": "1-6", "description": "Переадресация без запроса - вызов принят другим оператором"},
        {"id": 7, "name": "1-7", "description": "Переадресация без запроса - вызов не принят другим оператором"},
        {"id": 8, "name": "1-8", "description": "Прямой набор номера - абонент не ответил"},
        {"id": 9, "name": "1-9", "description": "Прямой набор номера - 151 звонит абоненту +375296254070, система сначала пытается согласно диалплану набрать номер с линии 0001363, но она оказалась занята другим разговором,\nи звонок согласно настройкам пошел с резервной линии 0001366, трубку подняли"},
        {"id": 10, "name": "1-10", "description": "Прямой набор номера - 151 звонит абоненту +375296254070, система пытается согласно диалплану сделать звонок с линии 0001363, но она\nзанята другим разговором, а иных резервных линий для абонента 151 не предусмотрено, и звонок прервался"},
        {"id": 11, "name": "1-11", "description": "151-й абонент пытается сделать вызов по направлению (+7), куда вызовы ему запрещены (ему разрешены только +375), и получает отказ в наборе номера\n+74957776644"},
        
        # Входящие (2-X)
        {"id": 12, "name": "2-1", "description": "Прослушивание приветствия, набор внутренних номеров - ответ"},
        {"id": 13, "name": "2-2", "description": "Прослушивание приветствия, набор внутренних номеров - нет ответа"},
        {"id": 14, "name": "2-3", "description": "Звонок без приветствия - набор внутренних номеров - ответ"},
        {"id": 15, "name": "2-4", "description": "Звонок без приветствия - набор внутренних номеров - нет ответа"},
        {"id": 16, "name": "2-5", "description": "Звонок на группу абонентов + мобильный = подняли трубку на мобильном"},
        {"id": 17, "name": "2-6", "description": "Звонок на группу абонентов + мобильный = подняли трубку на внутреннем"},
        {"id": 18, "name": "2-7", "description": "Звонок на группу абонентов - подняли трубку - Переадресация вызова с запросом - вызов принят другим оператором - разговор отклонен"},
        {"id": 19, "name": "2-8", "description": "Звонок на группу абонентов - подняли трубку - Переадресация вызова с запросом - вызов принят другим оператором - разговор состоялся"},
        {"id": 20, "name": "2-9", "description": "Звонок на группу абонентов - подняли трубку - Переадресация вызова с запросом - вызов не принят другим оператором (нет на месте)"},
        {"id": 21, "name": "2-10", "description": "Звонок на группу абонентов - подняли трубку - Переадресация без запроса - вызов принят другим оператором"},
        {"id": 22, "name": "2-11", "description": "Звонок на группу абонентов - подняли трубку - Переадресация без запроса - вызов не принят другим оператором"},
        {"id": 23, "name": "2-12", "description": "Звонок на одного абонента, потом добавился еще один абонент - трубку подняли"},
        {"id": 24, "name": "2-13", "description": "Звонок на одного абонента, потом добавился еще один абонент, потом добавился мобильник менеджера - трубку подняли на внутреннем"},
        {"id": 25, "name": "2-14", "description": "Звонок на одного абонента, потом добавился еще один абонент, потом добавился мобильник менеджера - трубку подняли на мобильном"},
        {"id": 26, "name": "2-15", "description": "Звонок на одного абонента, потом добавился еще один абонент, потом добавился мобильник менеджера - трубку не подняли нигде"},
        {"id": 27, "name": "2-16", "description": "Звонок - приветствие - звонок на одного - приветствие - звонок на двух - приветствие - звонок на 2-х + мобильный - ответ на внутреннем"},
        {"id": 28, "name": "2-17", "description": "Звонок - приветствие - звонок на одного - приветствие - звонок на двух - приветствие - звонок на 2-х + мобильный - ответ на мобильном"},
        {"id": 29, "name": "2-18", "description": "Внешний звонок → отвечает 151-й 2. 151 переводит на 152-го (с запросом) 3. 152 принимает, но переводит дальше на 150-го 4. 150 разговаривает с клиентом"},
        {"id": 30, "name": "2-19", "description": "Поступил звонок - 151-й поднял трубку, отправил звонок с безусловной переадресацией 152-му, но 152-й в этот момент сам разговаривал (был занят) (телефонный аппарат менеджера умеет принимать второй вызов во время первого)"},
        {"id": 31, "name": "2-20", "description": "Поступил звонок - 151-й поднял трубку, отправил звонок с условной переадресацией 152-му, но 152-й в этот момент сам разговаривал (был занят) (телефонный аппарат менеджера умеет принимать второй вызов во время первого)"},
        {"id": 32, "name": "2-21", "description": "Поступил звонок - 151-й поднял трубку, отправил звонок с безусловной переадресацией 152-му, но 152-й в этот момент сам разговаривал (был занят) (телефонный аппарат менеджера не умеет принимать второй вызов во время первого)"},
        {"id": 33, "name": "2-22", "description": "Поступил звонок - 151-й поднял трубку, отправил звонок с условной переадресацией 152-му, но 152-й в этот момент сам разговаривал (был занят) (телефонный аппарат менеджера не умеет принимать второй вызов во время первого)"},
        {"id": 34, "name": "2-23", "description": "Поступил звонок - поднял 151, и сделал переадресацию с запросом на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, подняли на +375296254070 и согласились поговорить"},
        {"id": 35, "name": "2-24", "description": "Поступил звонок - поднял 151, и сделал переадресацию с запросом на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, подняли на +375296254070 и не согласились поговорить"},
        {"id": 36, "name": "2-25", "description": "Поступил звонок - поднял 151, и сделал переадресацию с запросом на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, подняли на 150 и согласились поговорить"},
        {"id": 37, "name": "2-26", "description": "Поступил звонок - поднял 151, и сделал переадресацию с запросом на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, подняли на 150 и не согласились поговорить"},
        {"id": 38, "name": "2-27", "description": "Поступил звонок - поднял 151, и сделал переадресацию без запроса на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, подняли на 150 и поговорили"},
        {"id": 39, "name": "2-28", "description": "Поступил звонок - поднял 151, и сделал переадресацию без запроса на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, подняли на +375296254070 и поговорили"},
        {"id": 40, "name": "2-29", "description": "Поступил звонок - поднял 151, и сделал переадресацию без запроса на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, нигде трубку не подняли"},
        {"id": 41, "name": "2-30", "description": "Поступил звонок - поднял 151, и сделал переадресацию с запросом на номер 300 (FollowMe номер менеджера) - звонок пошел сначала на 150, потом на 150 и +375296254070, нигде трубку не подняли"},
        
        # Внутренние (3-X)
        {"id": 42, "name": "3-1", "description": "Звонок на абонента - поднял трубку"},
        {"id": 43, "name": "3-2", "description": "Звонок на абонента - не поднял трубку"},
        {"id": 44, "name": "3-3", "description": "Звонок на отдел - подняли трубку"},
        {"id": 45, "name": "3-4", "description": "Звонок на отдел - не подняли трубку"}
        ]
    
    # Статусы звонков
    call_statuses = [
        {"id": 2, "name": "Ответили", "icon": "✅"},
        {"id": 3, "name": "Не ответили", "icon": "❌"}
    ]
    
    # Преобразуем словари в списки для шаблона
    managers_list = []
    for phone, manager_data in enterprise_data.get('managers', {}).items():
        managers_list.append({
            'phone': phone,
            'name': manager_data['name'],
            'clean_name': manager_data.get('clean_name', manager_data['name']),  # Имя без внутреннего номера
            'personal_phone': manager_data.get('personal_phone', ''),
            'follow_me_number': manager_data.get('follow_me_number'),
            'follow_me_enabled': manager_data.get('follow_me_enabled', False)
        })
    
    lines_list = []
    for line_id, line_data in enterprise_data.get('lines', {}).items():
        lines_list.append({
            'id': line_id,
            'name': line_data['name'],
            'phone': line_data.get('phone', ''),
            'operator': line_data.get('operator', '')
        })
    
    return templates.TemplateResponse("test_interface.html", {
        "request": request,
        "enterprise_number": enterprise,
        "enterprise_name": enterprise_data['name'],
        "call_types": call_types,
        "call_statuses": call_statuses,
        "managers": managers_list,
        "lines": lines_list
    })

@app.post("/api/webhook-event")
async def webhook_event(request: Request):
    """
    Endpoint для получения событий от эмулятора
    """
    try:
        # Получаем JSON данные
        event_data = await request.json()
        
        # logger.info(f"📡 Получено событие: {event_data.get('event', 'UNKNOWN')} для звонка {event_data.get('call_id', 'unknown')}")
        
        # Здесь будет логика обработки события
        # Пока просто логируем и возвращаем успех
        
        return {
            "success": True,
            "message": "Событие успешно обработано",
            "event_id": event_data.get('call_id'),
            "timestamp": event_data.get('timestamp')
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки события: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Ошибка обработки события: {str(e)}")

async def proxy_webhook_handler(event_type: str, request: Request):
    """Прокси для отправки событий на внешний webhook bot.vochi.by"""
    import httpx
    
    # Получаем данные события
    event_data = await request.json()
    
    # Получаем заголовки из оригинального запроса
    headers = dict(request.headers)
    
    # Формируем заголовки для внешнего запроса
    proxy_headers = {
        'Content-Type': 'application/json',
    }
    
    # Добавляем Token если есть
    if 'Token' in headers:
        proxy_headers['Token'] = headers['Token']
    elif 'token' in headers:
        proxy_headers['Token'] = headers['token']
    elif event_data.get('Token'):
        proxy_headers['Token'] = event_data['Token']
    
    try:
        # Отправляем на внешний webhook
        external_url = f"https://bot.vochi.by/{event_type.lower()}"
        
        # Подробное логирование события
        logger.info("=" * 80)
        logger.info(f"🚀 ЭМУЛИРОВАННОЕ СОБЫТИЕ: {event_type.upper()}")
        logger.info(f"📡 URL назначения: {external_url}")
        logger.info(f"📋 Полные данные события:")
        logger.info(f"{json.dumps(event_data, ensure_ascii=False, indent=2)}")
        logger.info(f"📋 Заголовки запроса:")
        logger.info(f"{json.dumps(proxy_headers, ensure_ascii=False, indent=2)}")
        logger.info("=" * 80)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                external_url,
                json=event_data,
                headers=proxy_headers
            )
            
            # Получаем ответ
            response_content = response.content
            
            # Пытаемся распарсить JSON
            try:
                response_data = response.json()
            except:
                response_data = {"raw_response": response_content.decode('utf-8', errors='ignore')}
            
            # Подробное логирование ответа
            logger.info("-" * 80)
            logger.info(f"✅ ОТВЕТ ОТ СЕРВЕРА: {event_type.upper()}")
            logger.info(f"📡 HTTP статус: {response.status_code}")
            logger.info(f"📋 Полное тело ответа:")
            logger.info(f"{json.dumps(response_data, ensure_ascii=False, indent=2)}")
            logger.info(f"📋 Заголовки ответа:")
            logger.info(f"{dict(response.headers)}")
            logger.info("-" * 80)
            
            # Возвращаем ответ с теми же данными что пришли от внешнего сервиса
            return JSONResponse(
                content=response_data,
                status_code=response.status_code,
                headers={"X-Proxy-Status": str(response.status_code)}
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка прокси webhook: {e}")
        return JSONResponse(
            content={"error": str(e), "proxy_error": True},
            status_code=500
        )

# Создаем прокси endpoint для каждого типа события
@app.post("/api/proxy-webhook/{event_type}")
async def proxy_webhook(event_type: str, request: Request):
    return await proxy_webhook_handler(event_type, request)

@app.post("/api/log-event")
async def log_event(request: Request):
    """Эндпоинт для логирования событий эмуляции в файл"""
    try:
        log_data = await request.json()
        
        # Формируем лог в том же формате что и в прокси-функции
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        
        if log_data.get('type') == 'emulation_request':
            logger.info(f"🚀 EMULATION REQUEST to {log_data.get('url', 'unknown')}")
            logger.info(f"📤 Event Type: {log_data.get('event_type', 'unknown')}")
            logger.info(f"📊 Event Data: {json.dumps(log_data.get('data', {}), ensure_ascii=False, indent=2)}")
            
        elif log_data.get('type') == 'emulation_response':
            logger.info(f"📥 EMULATION RESPONSE for {log_data.get('event_type', 'unknown')}")
            logger.info(f"📊 Response Status: {log_data.get('status', 'unknown')}")
            logger.info(f"📊 Response Data: {json.dumps(log_data.get('response_data', {}), ensure_ascii=False, indent=2)}")
            
        elif log_data.get('type') == 'emulation_error':
            logger.error(f"❌ EMULATION ERROR for {log_data.get('event_type', 'unknown')}")
            logger.error(f"❌ Error: {log_data.get('error', 'unknown')}")
        
        # Явно добавляем CORS заголовки к ответу
        response = JSONResponse({"status": "logged"})
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response
        
    except Exception as e:
        logger.error(f"Failed to log event: {e}")
        error_response = JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        error_response.headers["Access-Control-Allow-Origin"] = "*"
        return error_response

@app.post("/api/test-call")
async def test_call_api(
    call_type: int = Form(...),
    external_phone: str = Form(...),
    internal_phone: str = Form(...),
    line_id: str = Form(...),
    call_status: int = Form(None),  # Опциональный, будет определен автоматически
    duration_minutes: int = Form(None),  # Опциональный, будет определен автоматически
    enterprise: str = Form(...)
):
    """API для запуска тестового звонка"""
    
    try:
        # Маппинг дефолтных значений для типов звонков
        call_type_defaults = {
            1: {"duration": 3, "status": 2},  # 1-1: 3 мин, ответили (2)
            2: {"duration": 3, "status": 2},  # 1-2: 3 мин, ответили (2)
            3: {"duration": 3, "status": 2},  # 1-3: 3 мин, ответили (2)
            4: {"duration": 3, "status": 2},  # 1-4: 3 мин, ответили (2)
            5: {"duration": 3, "status": 2},  # 1-5: 3 мин, ответили (2)
            6: {"duration": 3, "status": 2},  # 1-6: 3 мин, ответили (2)
            7: {"duration": 3, "status": 2},  # 1-7: 3 мин, ответили (2)
            8: {"duration": 3, "status": 3},  # 1-8: 3 мин, НЕ ответили (3)
            9: {"duration": 3, "status": 2},  # 1-9: 3 мин, ответили (2)
            10: {"duration": 3, "status": 4}, # 1-10: 3 мин, ОТМЕНЕН (4)
            11: {"duration": 3, "status": 5}, # 1-11: 3 мин, ЗАПРЕЩЕН (5)
            12: {"duration": 3, "status": 2}, # 2-1: 3 мин, ответили (2)
            13: {"duration": 3, "status": 3}, # 2-2: 3 мин, НЕ ответили (3)
            14: {"duration": 3, "status": 2}, # 2-3: 3 мин, ответили (2)
            15: {"duration": 3, "status": 3}, # 2-4: 3 мин, НЕ ответили (3)
            16: {"duration": 3, "status": 2}, # 2-5: 3 мин, ответили на мобильном (2)
            17: {"duration": 3, "status": 2}, # 2-6: 3 мин, ответили на внутреннем (2)
            18: {"duration": 3, "status": 3}, # 2-7: 3 мин, разговор отклонен (3)
            19: {"duration": 3, "status": 2}, # 2-8: 3 мин, разговор состоялся (2)
            20: {"duration": 2, "status": 3}, # 2-9: 2 мин, не принят (3)
            21: {"duration": 3, "status": 2}, # 2-10: 3 мин, принят (2)
            22: {"duration": 2, "status": 3}, # 2-11: 2 мин, не принят (3)
            23: {"duration": 3, "status": 2}, # 2-12: 3 мин, ответили (2)
            24: {"duration": 3, "status": 2}, # 2-13: 3 мин, ответили на внутреннем (2)
            25: {"duration": 3, "status": 2}, # 2-14: 3 мин, ответили на мобильном (2)
            26: {"duration": 2, "status": 3}, # 2-15: 2 мин, НЕ ответили нигде (3)
            27: {"duration": 3, "status": 2}, # 2-16: 3 мин, ответили на внутреннем (2)
            28: {"duration": 3, "status": 2}, # 2-17: 3 мин, ответили на мобильном (2)
            29: {"duration": 4, "status": 2}, # 2-18: 4 мин, ответили (последовательная переадресация) (2)
            30: {"duration": 3, "status": 2}, # 2-19: 3 мин, ответили (второй вызов принят, аппарат умеет) (2)
            31: {"duration": 3, "status": 2}, # 2-20: 3 мин, ответили (второй вызов принят, аппарат умеет) (2)
            32: {"duration": 2, "status": 3}, # 2-21: 2 мин, НЕ ответили (аппарат не умеет второй вызов) (3)
            33: {"duration": 2, "status": 3}, # 2-22: 2 мин, НЕ ответили (аппарат не умеет второй вызов) (3)
            34: {"duration": 4, "status": 2}, # 2-23: 4 мин, ответили (Follow Me согласились) (2)
            35: {"duration": 3, "status": 3}, # 2-24: 3 мин, НЕ ответили (Follow Me не согласились) (3)
            36: {"duration": 4, "status": 2}, # 2-25: 4 мин, ответили (подняли на 150 и согласились) (2)
            37: {"duration": 3, "status": 3}, # 2-26: 3 мин, НЕ ответили (подняли на 150 и не согласились) (3)
            38: {"duration": 3, "status": 2}, # 2-27: 3 мин, ответили (без запроса на 150) (2)
            39: {"duration": 3, "status": 2}, # 2-28: 3 мин, ответили (без запроса на мобильном) (2)
            40: {"duration": 2, "status": 3}, # 2-29: 2 мин, НЕ ответили (без запроса нигде) (3)
            41: {"duration": 2, "status": 3}, # 2-30: 2 мин, НЕ ответили (с запросом нигде) (3)
            42: {"duration": 2, "status": 2}, # 3-1: 2 мин, ответили (внутренний звонок) (2)
            43: {"duration": 1, "status": 3}, # 3-2: 1 мин, НЕ ответили (внутренний звонок) (3)
            44: {"duration": 3, "status": 2}, # 3-3: 3 мин, ответили (звонок на отдел) (2)
            45: {"duration": 2, "status": 3}, # 3-4: 2 мин, НЕ ответили (звонок на отдел) (3)
            # Добавим остальные типы по мере необходимости
        }
        
        # Проверяем предприятие
        if enterprise not in test_service.enterprises_cache:
            raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
            
        enterprise_data = test_service.enterprises_cache[enterprise]
        
        # Устанавливаем дефолтные значения на основе типа звонка
        defaults = call_type_defaults.get(call_type, {"duration": 3, "status": 2})
        if duration_minutes is None:
            duration_minutes = defaults["duration"]
        if call_status is None:
            call_status = defaults["status"]
        
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
async def get_managers(enterprise: str):
    """API для получения списка менеджеров"""
    if enterprise not in test_service.enterprises_cache:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise} не найдено")
    
    # Загружаем РЕАЛЬНЫЕ данные из БД
    if not await test_service.load_enterprise_data(enterprise):
        raise HTTPException(status_code=500, detail="Ошибка загрузки данных менеджеров")
    
    return JSONResponse(test_service.enterprises_cache[enterprise].get('managers', {}))

@app.get("/api/lines") 
async def get_lines(enterprise: str):
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
    uvicorn.run(app, host="0.0.0.0", port=8025)
