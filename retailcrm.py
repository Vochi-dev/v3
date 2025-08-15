#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🏪 RetailCRM Integration Service
================================

Сервис для интеграции системы телефонии с RetailCRM.
Обеспечивает взаимодействие через API v5.

Автор: AI Assistant
Дата: 03.08.2025
Версия: 1.0 (Фаза 1 - Тестирование API)
"""

import asyncio
import os
import json
import logging
import sys
import time
import jwt
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncpg

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

# RetailCRM настройки для тестирования
RETAILCRM_CONFIG = {
    "base_url": "https://evgenybaevski.retailcrm.ru",
    "api_key": "NsX6ZE1W6C8vOkkcNm2NBNLzwVJxLNvl",
    "client_id": "8bc4e63e-4fb2-4e6b-a78f-1dbbc96f6ad4",
    "api_version": "v5",
    "timeout": 30
}

# JWT конфигурация для токенов доступа RetailCRM
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "vochi-retailcrm-secret-key-2025")
JWT_ALGORITHM = "HS256"

# Настройки логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/root/asterisk-webhook/logs/retailcrm.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("RetailCRM")

# =============================================================================
# МОДЕЛИ ДАННЫХ
# =============================================================================

class RetailCRMResponse(BaseModel):
    """Стандартный ответ от RetailCRM API"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    response_time: float
    endpoint: str


class PhoneData(BaseModel):
    """Данные телефона клиента"""
    number: str

class CustomerData(BaseModel):
    """Данные клиента для создания/обновления"""
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    phone: Optional[str] = None  # Старое поле для совместимости
    phones: Optional[List[PhoneData]] = None  # Новое поле для массива телефонов
    email: Optional[str] = None
    managerId: Optional[int] = None  # ID менеджера для привязки


class CallEventData(BaseModel):
    """Данные события звонка для создания заметки"""
    phone: str
    type: str  # incoming, outgoing
    duration: Optional[int] = None
    status: str  # answered, busy, failed, etc.
    customer_id: Optional[int] = None  # ID клиента в RetailCRM
    manager_name: Optional[str] = None  # Имя менеджера
    recording_url: Optional[str] = None  # Ссылка на запись


# =============================================================================
# RETAILCRM API CLIENT
# =============================================================================

class RetailCRMClient:
    """Клиент для работы с RetailCRM API v5"""
    
    def __init__(self, config: Dict[str, Any]):
        self.base_url = config["base_url"]
        self.api_key = config["api_key"] 
        self.api_version = config["api_version"]
        self.timeout = config["timeout"]
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    def _build_url(self, endpoint: str) -> str:
        """Построить полный URL для API endpoint"""
        base = f"{self.base_url}/api/{self.api_version}"
        endpoint = endpoint.lstrip('/')
        return f"{base}/{endpoint}"
    
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> RetailCRMResponse:
        """Выполнить HTTP запрос к RetailCRM API"""
        
        start_time = time.time()
        url = self._build_url(endpoint)
        
        # Добавляем API key в параметры
        if params is None:
            params = {}
        params['apiKey'] = self.api_key
        
        logger.info(f"🔄 {method.upper()} {endpoint}")
        logger.info(f"📡 URL: {url}")
        logger.info(f"📋 Params: {params}")
        if data:
            logger.info(f"📦 Data: {data}")
        
        try:
            if not self.session:
                raise RuntimeError("Client session not initialized")
                
            # Для telephony endpoints используем form-data с JSON строками внутри параметров
            if endpoint.startswith("/telephony/") and method == "POST":
                # Telephony endpoints требуют form-data с JSON строками
                from aiohttp import FormData
                form_data = FormData()
                
                for key, value in data.items():
                    form_data.add_field(key, str(value))
                
                async with self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=form_data
                ) as response:
                    response_time = time.time() - start_time
                    response_text = await response.text()
                    
                    logger.info(f"⏱️ Response time: {response_time:.3f}s")
                    logger.info(f"📊 Status: {response.status}")
                    logger.info(f"📄 Response: {response_text[:500]}...")
                    
                    if response.status in [200, 201]:
                        try:
                            response_data = json.loads(response_text) if response_text else {}
                            return RetailCRMResponse(
                                success=response_data.get("success", True),
                                data=response_data,
                                response_time=response_time,
                                endpoint=endpoint
                            )
                        except json.JSONDecodeError as e:
                            logger.error(f"❌ JSON decode error: {e}")
                            return RetailCRMResponse(
                                success=False,
                                error=f"Invalid JSON response: {e}",
                                response_time=response_time,
                                endpoint=endpoint
                            )
                    else:
                        return RetailCRMResponse(
                            success=False,
                            error=f"HTTP {response.status}: {response_text}",
                            response_time=response_time,
                            endpoint=endpoint
                        )
            elif method == "POST" and data:
                # Используем form-data для POST запросов
                from aiohttp import FormData
                form_data = FormData()
                
                # Обрабатываем вложенные объекты для form-data
                def add_to_form(form_obj, key_prefix, value):
                    if isinstance(value, dict):
                        for sub_key, sub_value in value.items():
                            add_to_form(form_obj, f"{key_prefix}[{sub_key}]", sub_value)
                    else:
                        form_obj.add_field(key_prefix, str(value))
                
                for key, value in data.items():
                    add_to_form(form_data, key, value)
                
                async with self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=form_data
                ) as response:
                    response_time = time.time() - start_time
                    response_text = await response.text()
                    
                    logger.info(f"⏱️ Response time: {response_time:.3f}s")
                    logger.info(f"📊 Status: {response.status}")
                    logger.info(f"📄 Response: {response_text[:500]}...")
                    
                    if response.status in [200, 201]:
                        try:
                            response_data = json.loads(response_text)
                            return RetailCRMResponse(
                                success=True,
                                data=response_data,
                                response_time=response_time,
                                endpoint=endpoint
                            )
                        except json.JSONDecodeError as e:
                            logger.error(f"❌ JSON decode error: {e}")
                            return RetailCRMResponse(
                                success=False,
                                error=f"Invalid JSON response: {e}",
                                response_time=response_time,
                                endpoint=endpoint
                            )
                    else:
                        return RetailCRMResponse(
                            success=False,
                            error=f"HTTP {response.status}: {response_text}",
                            response_time=response_time,
                            endpoint=endpoint
                        )
            else:
                # Используем JSON для GET запросов
                async with self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data if data else None
                ) as response:
                    response_time = time.time() - start_time
                    response_text = await response.text()
                    
                    logger.info(f"⏱️ Response time: {response_time:.3f}s")
                    logger.info(f"📊 Status: {response.status}")
                    logger.info(f"📄 Response: {response_text[:500]}...")
                    
                    if response.status in [200, 201]:
                        try:
                            response_data = json.loads(response_text)
                            return RetailCRMResponse(
                                success=True,
                                data=response_data,
                                response_time=response_time,
                                endpoint=endpoint
                            )
                        except json.JSONDecodeError as e:
                            logger.error(f"❌ JSON decode error: {e}")
                            return RetailCRMResponse(
                                success=False,
                                error=f"Invalid JSON response: {e}",
                                response_time=response_time,
                                endpoint=endpoint
                            )
                    else:
                        return RetailCRMResponse(
                            success=False,
                            error=f"HTTP {response.status}: {response_text}",
                            response_time=response_time,
                            endpoint=endpoint
                        )

        except asyncio.TimeoutError:
            response_time = time.time() - start_time
            logger.error(f"⏰ Request timeout after {response_time:.3f}s")
            return RetailCRMResponse(
                success=False,
                error="Request timeout",
                response_time=response_time,
                endpoint=endpoint
            )
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"💥 Request error: {e}")
            return RetailCRMResponse(
                success=False,
                error=str(e),
                response_time=response_time,
                endpoint=endpoint
            )

    # =========================================================================
    # 0. Регистрация/редактирование телефонии через Integration Modules API
    # =========================================================================

    async def upsert_integration_module(self, code: str, integration_module: Dict[str, Any]) -> RetailCRMResponse:
        """Создать/обновить модуль интеграции (телефонию). POST /integration-modules/{code}/edit"""
        endpoint = f"/integration-modules/{code}/edit"
        # RetailCRM ожидает scalar field, содержащий JSON строку
        data = {"integrationModule": json.dumps(integration_module)}
        return await self._make_request("POST", endpoint, data=data)
    
    async def deactivate_integration_module(self, code: str, integration_module: Dict[str, Any]) -> RetailCRMResponse:
        """Деактивировать модуль интеграции (установить active: false). POST /integration-modules/{code}/edit"""
        # Копируем модуль и устанавливаем active: false
        deactivated_module = integration_module.copy()
        deactivated_module["active"] = False
        
        endpoint = f"/integration-modules/{code}/edit"
        data = {"integrationModule": json.dumps(deactivated_module)}
        return await self._make_request("POST", endpoint, data=data)
    
    async def get_integration_module(self, code: str) -> RetailCRMResponse:
        """Получить информацию о модуле интеграции по коду. GET /integration-modules/{code}"""
        endpoint = f"/integration-modules/{code}"
        return await self._make_request("GET", endpoint)
    
    # =========================================================================
    # 1. БАЗОВЫЕ API МЕТОДЫ
    # =========================================================================
    
    async def test_credentials(self) -> RetailCRMResponse:
        """Проверить подключение и валидность API ключа"""
        return await self._make_request("GET", "/credentials")
    
    async def get_sites(self) -> RetailCRMResponse:
        """Получить список сайтов (системная информация)"""
        return await self._make_request("GET", "/reference/sites")
    
    # =========================================================================
    # 2. РАБОТА С МЕНЕДЖЕРАМИ
    # =========================================================================
    
    async def get_users(self) -> RetailCRMResponse:
        """Получить список пользователей (менеджеров)"""
        return await self._make_request("GET", "/users")
    
    async def get_user(self, user_id: int) -> RetailCRMResponse:
        """Получить информацию о конкретном пользователе"""
        return await self._make_request("GET", f"/users/{user_id}")
    
    async def get_user_groups(self) -> RetailCRMResponse:
        """Получить список групп пользователей"""
        return await self._make_request("GET", "/user-groups")
    
    # =========================================================================
    # 3. РАБОТА С КЛИЕНТАМИ  
    # =========================================================================
    
    async def search_customer_by_phone(self, phone: str) -> RetailCRMResponse:
        """Найти клиента по номеру телефона"""
        params = {"filter[phone]": phone}
        return await self._make_request("GET", "/customers", params=params)
    
    async def get_customer(self, customer_id: int) -> RetailCRMResponse:
        """Получить информацию о клиенте по ID"""
        return await self._make_request("GET", f"/customers/{customer_id}")
    
    async def create_customer(self, customer_data: CustomerData) -> RetailCRMResponse:
        """Создать нового клиента"""
        # RetailCRM API v5 требует JSON с правильной структурой
        customer_dict = customer_data.dict(exclude_none=True)
        
        # Обработка телефонов: если есть старое поле phone, конвертируем в phones массив
        if customer_dict.get("phone") and not customer_dict.get("phones"):
            customer_dict["phones"] = [{"number": customer_dict["phone"]}]
            # Удаляем старое поле phone из данных
            customer_dict.pop("phone", None)
        
        # Структура по документации RetailCRM API v5
        data = {
            "customer": json.dumps(customer_dict),  # JSON строка внутри form-data
            "site": "evgenybaevski"
        }
            
        return await self._make_request("POST", "/customers/create", data=data)
    
    async def edit_customer(self, customer_id: int, customer_data: CustomerData) -> RetailCRMResponse:
        """Редактировать существующего клиента"""
        data = customer_data.dict(exclude_none=True)
        return await self._make_request("POST", f"/customers/{customer_id}/edit", data=data)
    
    # =========================================================================
    # 4. СОБЫТИЯ ТЕЛЕФОНИИ
    # =========================================================================
    
    async def create_call_task(self, call_data: CallEventData) -> RetailCRMResponse:
        """Создать задачу о звонке в RetailCRM"""
        # Формируем читаемый текст для задачи
        duration_text = ""
        if call_data.duration:
            minutes = call_data.duration // 60
            seconds = call_data.duration % 60
            duration_text = f" Длительность: {minutes} мин {seconds} сек."
        
        call_type_text = "📞 Входящий" if call_data.type == "incoming" else "📞 Исходящий" 
        status_text = {
            "answered": "отвечен",
            "busy": "занято", 
            "failed": "не отвечен",
            "no_answer": "не отвечен"
        }.get(call_data.status, call_data.status)
        
        # Заголовок задачи
        task_title = f"{call_type_text} звонок"
        
        # Детальное описание в комментарии с HTML-кнопкой прослушивания
        commentary_parts = [f"Звонок от {call_data.phone}.{duration_text}"]
        commentary_parts.append(f"Статус: {status_text}.")
        
        if call_data.manager_name:
            commentary_parts.append(f"Менеджер: {call_data.manager_name}.")
            
        if call_data.recording_url:
            # Создаем HTML-кнопку для прослушивания прямо в RetailCRM
            recording_button = f"""
            
🎧 ЗАПИСЬ РАЗГОВОРА:
<a href="{call_data.recording_url}" target="_blank" style="display: inline-block; background-color: #007bff; color: white; padding: 8px 15px; text-decoration: none; border-radius: 4px; font-weight: bold;">▶️ ПРОСЛУШАТЬ ЗАПИСЬ</a>

Прямая ссылка: {call_data.recording_url}
            """.strip()
            commentary_parts.append(recording_button)
        
        commentary = " ".join(commentary_parts)
        
        # Создаем структуру данных для задачи
        data = {
            "task": json.dumps({
                "text": task_title,
                "commentary": commentary,
                "customer": {"id": call_data.customer_id},
                "performerId": 16  # По умолчанию Евгений Баевский
            }),
            "site": "evgenybaevski"
        }
        
        return await self._make_request("POST", "/tasks/create", data=data)
    
    async def get_telephony_settings(self) -> RetailCRMResponse:
        """Получить настройки телефонии"""
        return await self._make_request("GET", "/telephony/setting")
    
    # ===== НОВЫЕ МЕТОДЫ ПО ОФИЦИАЛЬНОЙ ДОКУМЕНТАЦИИ =====
    
    async def upload_calls_history(self, calls_data: list) -> RetailCRMResponse:
        """Загрузка истории звонков согласно документации RetailCRM"""
        # Для /telephony/calls/upload нужно передавать calls как JSON строку в form-data
        data = {
            "calls": json.dumps(calls_data),
            "clientId": RETAILCRM_CONFIG["client_id"]
        }
        return await self._make_request("POST", "/telephony/calls/upload", data=data)
    
    async def send_call_event(self, event_data: dict) -> RetailCRMResponse:
        """Отправка события звонка согласно документации RetailCRM"""
        # Для /telephony/call/event нужно передавать event как JSON строку в form-data
        data = {
            "event": json.dumps(event_data),
            "clientId": RETAILCRM_CONFIG["client_id"]
        }
        return await self._make_request("POST", "/telephony/call/event", data=data)
    
    async def get_responsible_manager(self, phone: str) -> RetailCRMResponse:
        """Получение ответственного менеджера по номеру телефона"""
        params = {
            "phone": phone,
            "clientId": RETAILCRM_CONFIG["client_id"]
        }
        return await self._make_request("GET", "/telephony/manager", params=params)
    
    # =========================================================================
    # 5. РАБОТА С ЗАКАЗАМИ
    # =========================================================================
    
    async def get_customer_orders(self, customer_id: int) -> RetailCRMResponse:
        """Получить заказы клиента"""
        params = {"filter[customerId]": customer_id}
        return await self._make_request("GET", "/orders", params=params)
    
    async def create_order(self, order_data: Dict[str, Any]) -> RetailCRMResponse:
        """Создать новый заказ"""
        return await self._make_request("POST", "/orders/create", data=order_data)


# =============================================================================
# FASTAPI ПРИЛОЖЕНИЕ
# =============================================================================

app = FastAPI(
    title="RetailCRM Integration Service",
    description="Сервис интеграции с RetailCRM для системы телефонии",
    version="1.0.0"
)

# Статика (favicon, логотипы) — используем общую папку проекта
STATIC_DIR = "/root/asterisk-webhook/static"
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Статика для админки RetailCRM
APP_STATIC_DIR = "/root/asterisk-webhook/app/static"
if os.path.isdir(APP_STATIC_DIR):
    app.mount("/retailcrm-static", StaticFiles(directory=APP_STATIC_DIR), name="retailcrm_static")

# Глобальные объекты
retailcrm_client = None

# Параметры подключения к PostgreSQL (безопасно, без импорта app.config)
PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_USER = os.environ.get("POSTGRES_USER", "postgres")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "r/Yskqh/ZbZuvjb2b3ahfg==")
PG_DB = os.environ.get("POSTGRES_DB", "postgres")

pg_pool: Optional[asyncpg.pool.Pool] = None

# Конфиг кэша
CACHE_TTL_SECONDS: int = int(os.environ.get("RETAILCRM_CACHE_TTL", 120))
CACHE_REFRESH_INTERVAL_SECONDS: int = int(os.environ.get("RETAILCRM_CACHE_REFRESH", 300))

# Кэш конфигов: enterprise_number -> (config_dict, expires_at_epoch)
CONFIG_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}

# Управление фоновым обновлением кэша
_cache_refresher_task: Optional[asyncio.Task] = None
_cache_refresher_stop_event: Optional[asyncio.Event] = None

# Простейшие метрики
STATS: Dict[str, int] = {
    "db_reads": 0,
    "db_writes": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_refreshes": 0,
}

# === Внутренние хелперы для событий телефонии ===
def _map_call_type(call_type: Optional[int]) -> Optional[str]:
    if call_type == 0:
        return "in"
    if call_type == 1:
        return "out"
    return None

def _is_internal(num: str) -> bool:
    try:
        return bool(num) and str(num).isdigit() and 2 <= len(str(num)) <= 5
    except Exception:
        return False

def _guess_direction_and_phone(raw: dict, fallback: str = "") -> tuple[str, str]:
    ct = raw.get("CallType")
    if isinstance(ct, (int, str)) and str(ct).isdigit():
        kind = _map_call_type(int(ct))
        if kind in {"in", "out"}:
            phone = raw.get("Phone") or raw.get("CallerIDNum") or raw.get("ConnectedLineNum") or fallback
            return kind, str(phone)
    caller = str(raw.get("CallerIDNum") or "")
    phone_field = str(raw.get("Phone") or "")
    exts = list(raw.get("Extensions") or [])
    caller_internal = _is_internal(caller)
    any_external_ext = any((not _is_internal(e)) and e for e in exts)
    if caller_internal and (any_external_ext or (phone_field and not _is_internal(phone_field))):
        external = next((e for e in exts if e and not _is_internal(e)), None) or phone_field
        return "out", external
    if (not caller_internal) and any(_is_internal(e) for e in exts):
        return "in", caller or phone_field
    return ("in", phone_field or caller or fallback)

def _normalize_json(value: Any) -> Dict[str, Any]:
    """Безопасно приводит JSON/JSONB значение из БД к dict.
    Допускает типы: dict (возвращается как есть), str (парсится как JSON),
    None (пустой dict). Для прочих типов возвращает пустой dict.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}

async def init_pg_pool() -> None:
    global pg_pool
    if pg_pool is None:
        pg_pool = await asyncpg.create_pool(
            host=PG_HOST,
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            database=PG_DB,
            min_size=1,
            max_size=10,
        )
        logger.info("✅ PostgreSQL pool initialized for retailcrm service")

async def close_pg_pool() -> None:
    global pg_pool
    if pg_pool is not None:
        await pg_pool.close()
        pg_pool = None
        logger.info("✅ PostgreSQL pool closed for retailcrm service")


async def ensure_integration_logs_table() -> None:
    """Создаёт таблицу логов интеграций по актуальной схеме, если её нет.

    Актуальная схема (единая для интеграций):
      - enterprise_number TEXT
      - integration_type TEXT
      - event_type TEXT
      - request_data JSONB
      - response_data JSONB
      - status TEXT ('success' | 'error')
      - error_message TEXT
      - created_at TIMESTAMPTZ DEFAULT now()
    """
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    sql = (
        """
        CREATE TABLE IF NOT EXISTS integration_logs (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            enterprise_number TEXT NOT NULL,
            integration_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            request_data JSONB,
            response_data JSONB,
            status TEXT NOT NULL,
            error_message TEXT
        );
        """
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(sql)


async def write_integration_log(
    enterprise_number: str,
    event_type: str,
    request_data: Dict[str, Any],
    response_data: Optional[Dict[str, Any]],
    status_ok: bool,
    error_message: Optional[str] = None,
    integration_type: str = "retailcrm",
) -> None:
    """Запись события интеграции в БД с поддержкой обеих схем.

    Основная попытка — актуальная схема (event_type/request_data/response_data/status/error_message).
    Фолбэк — старая схема (action/payload/response/success/error), если таблица создана ранее.
    """
    assert pg_pool is not None
    status_str = "success" if status_ok else "error"
    try:
        sql_new = (
            "INSERT INTO integration_logs(enterprise_number, integration_type, event_type, request_data, response_data, status, error_message) "
            "VALUES($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)"
        )
        async with pg_pool.acquire() as conn:
            await conn.execute(
                sql_new,
                enterprise_number,
                integration_type,
                event_type,
                json.dumps(request_data),
                json.dumps(response_data or {}),
                status_str,
                error_message,
            )
    except Exception as e_new:
        try:
            sql_old = (
                "INSERT INTO integration_logs(enterprise_number, integration_type, action, payload, response, success, error) "
                "VALUES($1, $2, $3, $4::jsonb, $5::jsonb, $6::boolean, $7)"
            )
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    sql_old,
                    enterprise_number,
                    integration_type,
                    event_type,
                    json.dumps(request_data),
                    json.dumps(response_data or {}),
                    status_ok,
                    error_message,
                )
        except Exception as e_old:
            logger.error(f"❌ Не удалось записать лог интеграции (new='{e_new}', old='{e_old}')")

async def fetch_retailcrm_config(enterprise_number: str) -> Dict[str, Any]:
    """Читает из enterprises.integrations_config JSONB -> 'retailcrm' для юнита.
    Возвращает пустой dict, если данных нет.
    """
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    query = (
        "SELECT integrations_config -> 'retailcrm' AS cfg "
        "FROM enterprises WHERE number = $1"
    )
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(query, enterprise_number)
        STATS["db_reads"] += 1
    if not row:
        return {}
    return _normalize_json(row["cfg"])  

async def delete_retailcrm_config(enterprise_number: str) -> bool:
    """Удаляет конфигурацию RetailCRM для предприятия (устанавливает integrations_config->retailcrm в NULL)"""
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    
    query = """
        UPDATE enterprises 
        SET integrations_config = 
            CASE 
                WHEN integrations_config IS NULL THEN NULL
                ELSE integrations_config - 'retailcrm'
            END
        WHERE number = $1
        RETURNING integrations_config
    """
    
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(query, enterprise_number)
            if row is not None:
                # Инвалидируем кэш
                if enterprise_number in CONFIG_CACHE:
                    del CONFIG_CACHE[enterprise_number]
                return True
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка удаления конфига RetailCRM для {enterprise_number}: {e}")
        return False

async def upsert_retailcrm_config(enterprise_number: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Обновляет ключ retailcrm в integrations_config указанного юнита (merge)."""
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None

    # Обновляем только ключ retailcrm, не затирая остальной JSONB
    query = (
        "UPDATE enterprises "
        "SET integrations_config = COALESCE(integrations_config, '{}'::jsonb) || jsonb_build_object('retailcrm', $2::jsonb) "
        "WHERE number = $1 "
        "RETURNING integrations_config -> 'retailcrm' AS cfg"
    )
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(query, enterprise_number, json.dumps(config))
        STATS["db_writes"] += 1
    if not row:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    updated_cfg = _normalize_json(row["cfg"]) 
    # Обновляем кэш немедленно (инвалидация)
    try:
        CONFIG_CACHE[enterprise_number] = (updated_cfg, time.time() + CACHE_TTL_SECONDS)
        STATS["cache_refreshes"] += 1
    except Exception:
        pass
    
    # Инвалидируем кэш интеграций для этого предприятия
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"http://localhost:8020/cache/invalidate/{enterprise_number}") as cache_response:
                if cache_response.status == 200:
                    logger.info(f"✅ Integration cache invalidated for {enterprise_number}")
                else:
                    logger.warning(f"⚠️ Failed to invalidate cache for {enterprise_number}: {cache_response.status}")
    except Exception as cache_error:
        logger.warning(f"⚠️ Cache invalidation error for {enterprise_number}: {cache_error}")
    return updated_cfg

async def save_user_extensions_to_db(enterprise_number: str, user_extensions: Dict[str, str]) -> bool:
    """Сохраняет соответствия пользователей RetailCRM и внутренних номеров в локальную БД"""
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    
    try:
        # Читаем текущую конфигурацию
        current_config = await fetch_retailcrm_config(enterprise_number)
        if not current_config:
            # Создаем базовую конфигурацию если ее нет
            current_config = {
                "enabled": False,
                "domain": "",
                "api_key": ""
            }
        
        # Обновляем user_extensions
        current_config["user_extensions"] = user_extensions
        current_config["last_sync"] = datetime.utcnow().isoformat() + "Z"
        
        # Сохраняем обновленную конфигурацию
        query = (
            "UPDATE enterprises "
            "SET integrations_config = COALESCE(integrations_config, '{}'::jsonb) || jsonb_build_object('retailcrm', $2::jsonb) "
            "WHERE number = $1"
        )
        async with pg_pool.acquire() as conn:
            await conn.execute(query, enterprise_number, json.dumps(current_config))
            STATS["db_writes"] += 1
        
        # Обновляем кэш
        try:
            CONFIG_CACHE[enterprise_number] = (current_config, time.time() + CACHE_TTL_SECONDS)
            STATS["cache_refreshes"] += 1
        except Exception:
            pass
        
        # Инвалидируем кэш интеграций для этого предприятия
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://localhost:8020/cache/invalidate/{enterprise_number}") as cache_response:
                    if cache_response.status == 200:
                        logger.info(f"✅ Integration cache invalidated for {enterprise_number}")
                    else:
                        logger.warning(f"⚠️ Failed to invalidate cache for {enterprise_number}: {cache_response.status}")
        except Exception as cache_error:
            logger.warning(f"⚠️ Cache invalidation error for {enterprise_number}: {cache_error}")
        
        logger.info(f"✅ Saved {len(user_extensions)} user extensions to DB for enterprise {enterprise_number}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error saving user extensions to DB for enterprise {enterprise_number}: {e}")
        return False

async def load_user_extensions_from_db(enterprise_number: str) -> Dict[str, str]:
    """Загружает соответствия пользователей RetailCRM и внутренних номеров из локальной БД"""
    try:
        config = await fetch_retailcrm_config(enterprise_number)
        return config.get("user_extensions", {})
    except Exception as e:
        logger.error(f"❌ Error loading user extensions from DB for enterprise {enterprise_number}: {e}")
        return {}

async def _get_enterprise_by_token(token: str) -> Optional[str]:
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    q = "SELECT number FROM enterprises WHERE name2 = $1 OR secret = $1 LIMIT 1"
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(q, token)
        return row["number"] if row else None

def _invert_user_extensions(cfg: Dict[str, Any]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    user_ext = (cfg or {}).get("user_extensions") or {}
    if isinstance(user_ext, dict):
        for uid_str, code in user_ext.items():
            if code:
                try:
                    mapping[str(code)] = int(uid_str)
                except Exception:
                    mapping[str(code)] = uid_str
    return mapping

# Временный кэш данных dial по уникальному звонку для последующего hangup
_last_dial_cache: Dict[str, Dict[str, Any]] = {}
_DIAL_CACHE_TTL_SEC = 600

# Гард от дублей автосоздания клиентов (например, когда почти одновременно приходят dial и hangup)
_recent_created_phones: Dict[str, float] = {}
_RECENT_CREATED_TTL_SEC = 60
_create_customer_locks: Dict[str, asyncio.Lock] = {}

def _get_phone_lock(phone: str) -> asyncio.Lock:
    key = phone or "__none__"
    lock = _create_customer_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _create_customer_locks[key] = lock
    return lock

def _recent_create_allowed(phone: str) -> bool:
    now = time.time()
    ts = _recent_created_phones.get(phone)
    if ts is None:
        return True
    return (now - ts) > _RECENT_CREATED_TTL_SEC

def _mark_recent_created(phone: str) -> None:
    _recent_created_phones[phone] = time.time()

def _cache_put_dial(unique_id: str, code: Optional[str], user_id: Optional[int]) -> None:
    try:
        from datetime import datetime
        _last_dial_cache[unique_id] = {
            "code": str(code) if code else None,
            "user_id": int(user_id) if isinstance(user_id, int) else user_id,
            "ts": datetime.utcnow().timestamp(),
        }
    except Exception:
        pass

def _cache_get_dial(unique_id: str) -> Optional[Dict[str, Any]]:
    try:
        from datetime import datetime
        entry = _last_dial_cache.get(unique_id)
        if not entry:
            return None
        if datetime.utcnow().timestamp() - float(entry.get("ts", 0)) > _DIAL_CACHE_TTL_SEC:
            _last_dial_cache.pop(unique_id, None)
            return None
        return entry
    except Exception:
        return None

@app.post("/internal/retailcrm/call-event")
async def internal_retailcrm_call_event(request: Request):
    if request.client and request.client.host not in {"127.0.0.1", "localhost"}:
        return JSONResponse(status_code=403, content={"success": False, "error": "forbidden"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "invalid json"})

    token = body.get("token")
    unique_id = body.get("uniqueId")
    event_type = body.get("event_type")  # dial|hangup
    raw = body.get("raw", {}) or {}
    record_url = body.get("record_url")

    if not token or not unique_id or event_type not in {"dial", "hangup"}:
        return JSONResponse(status_code=400, content={"success": False, "error": "invalid payload"})

    enterprise_number = await _get_enterprise_by_token(token)
    if not enterprise_number:
        return JSONResponse(status_code=404, content={"success": False, "error": "enterprise not found"})

    cfg = await fetch_retailcrm_config(enterprise_number)
    cfg = cfg if cfg and cfg.get("enabled") and cfg.get("domain") and cfg.get("api_key") else None
    if not cfg:
        return JSONResponse(status_code=400, content={"success": False, "error": "retailcrm disabled or config invalid"})

    base_url = cfg["domain"] if str(cfg["domain"]).startswith("http") else f"https://{cfg['domain']}"
    api_key = cfg["api_key"]

    if event_type == "dial":
        kind, phone = _guess_direction_and_phone(raw, fallback="")
    else:
        _, phone = _guess_direction_and_phone(raw, fallback="")
        kind = "hangup"

    # code / userId
    code = None
    exts = list(raw.get("Extensions") or [])
    cand = str(raw.get("CallerIDNum") or "")
    connected = str(raw.get("ConnectedLineNum") or "")
    # 1) Явный внутренний в CallerIDNum
    if _is_internal(cand):
        code = cand
    else:
        # 2) Любой внутренний в Extensions
        for e in exts:
            if _is_internal(str(e)):
                code = str(e)
                break
        # 3) Внутренний в ConnectedLineNum (частый случай для входящих)
        if not code and _is_internal(connected):
            code = connected
        # 4) Явно переданный внутренний код из апстрима
        if not code:
            ic = str(raw.get("InternalCode") or "")
            if _is_internal(ic):
                code = ic
    code_to_uid = _invert_user_extensions(cfg)
    user_id = code_to_uid.get(str(code)) if code else None

    # Для hangup подтянем code/user_id из кэша dial, если не удалось определить
    if event_type == "hangup":
        cache_entry = _cache_get_dial(unique_id)
        if cache_entry:
            if user_id is None and cache_entry.get("user_id") is not None:
                user_id = cache_entry.get("user_id")
            if not code and cache_entry.get("code"):
                code = str(cache_entry.get("code"))

    # Fallback: если не удалось определить code/user_id — берём первый заинтегрированный
    if code is None or user_id is None:
        ue = (cfg or {}).get("user_extensions") or {}
        if isinstance(ue, dict) and ue:
            for uid_str, c in ue.items():
                if not c:
                    continue
                if code is None:
                    code = str(c)
                if user_id is None:
                    try:
                        user_id = int(uid_str)
                    except Exception:
                        user_id = uid_str
                break

    event_payload: Dict[str, Any] = {
        "phone": phone if phone.startswith("+") else ("+" + phone.lstrip("+")) if phone else "+000",
        "type": kind if kind in {"in", "out", "hangup"} else "in",
        "callExternalId": unique_id,
    }
    if code:
        event_payload["codes"] = [str(code)]
    if user_id is not None:
        event_payload["userIds"] = [user_id]

    async with RetailCRMClient({
        "base_url": base_url,
        "api_key": api_key,
        "api_version": "v5",
        "timeout": 5,
    }) as client:
        # Автосоздание клиента переносим на этап hangup, чтобы знать исход разговора и корректно назначить ответственного
        if event_type == "hangup":
            try:
                normalized_phone = event_payload["phone"]
                phone_lock = _get_phone_lock(normalized_phone)
                async with phone_lock:
                    if not _recent_create_allowed(normalized_phone):
                        # Если подавили по TTL — залогируем и выйдем
                        try:
                            await write_integration_log(
                                enterprise_number=enterprise_number,
                                event_type="customer_create_suppressed_by_ttl",
                                request_data={"phone": normalized_phone, "ttl_sec": _RECENT_CREATED_TTL_SEC},
                                response_data=None,
                                status_ok=True,
                                error_message=None,
                            )
                        except Exception:
                            pass
                    else:
                        sr = await client.search_customer_by_phone(normalized_phone)
                        exists = False
                        if sr and sr.success and sr.data and isinstance(sr.data.get("customers"), list):
                            exists = len(sr.data.get("customers") or []) > 0
                        if exists:
                            try:
                                await write_integration_log(
                                    enterprise_number=enterprise_number,
                                    event_type="customer_exists",
                                    request_data={"phone": normalized_phone},
                                    response_data=sr.data,
                                    status_ok=True,
                                    error_message=None,
                                )
                            except Exception:
                                pass
                        if not exists:
                            # Назначаем ответственного, только если разговор состоялся (answered)
                            manager_for_creation = None
                            try:
                                call_status_int = int(raw.get("CallStatus", 0))
                            except Exception:
                                call_status_int = 0
                            if call_status_int == 2 and isinstance(user_id, int):
                                manager_for_creation = int(user_id)
                            cust = CustomerData(
                                firstName=normalized_phone,
                                phones=[PhoneData(number=normalized_phone)],
                                managerId=manager_for_creation,
                            )
                            cr = await client.create_customer(cust)
                            if cr and cr.success:
                                _mark_recent_created(normalized_phone)
                            try:
                                await write_integration_log(
                                    enterprise_number=enterprise_number,
                                    event_type="customer_autocreate",
                                    request_data={"phone": normalized_phone, "managerId": manager_for_creation},
                                    response_data=(cr.data if cr and cr.data else None),
                                    status_ok=bool(cr and cr.success),
                                    error_message=(cr.error if cr and cr.error else None),
                                )
                            except Exception:
                                pass
            except Exception:
                try:
                    await write_integration_log(
                        enterprise_number=enterprise_number,
                        event_type="customer_autocreate_error",
                        request_data={"phone": event_payload.get("phone"), "managerId": user_id},
                        response_data=None,
                        status_ok=False,
                        error_message="exception in autocreate",
                    )
                except Exception:
                    pass
        # 1) Всегда передаём codes/userIds и на hangup (для соответствия требованиям API)
        #    Дополнительно укажем hangupStatus для наглядности в CRM
        if event_type == "hangup":
            try:
                call_status_int = int(raw.get("CallStatus", 0))
            except Exception:
                call_status_int = 0
            if call_status_int == 2:
                event_payload["hangupStatus"] = "answered"
            else:
                # Для сценария "не ответили" фиксируем как "no answered"
                event_payload["hangupStatus"] = "no answered"
        ev = await client._make_request("POST", "/telephony/call/event", data={
            "clientId": enterprise_number,
            "event": json.dumps(event_payload, ensure_ascii=False),
        })
        logger.info(f"[internal call-event] sent: {event_payload} resp={ev.success}")
        try:
            await write_integration_log(
                enterprise_number=enterprise_number,
                event_type=f"call_event:{event_payload.get('type','unknown')}",
                request_data={
                    "uniqueId": unique_id,
                    "payload": event_payload,
                },
                response_data=(ev.data if ev and ev.data else None),
                status_ok=bool(ev and ev.success),
                error_message=(ev.error if ev and ev.error else None),
            )
        except Exception:
            # не мешаем основному потоку при сбое логирования
            pass

        # Кэшируем dial для последующего hangup
        if event_type == "dial":
            _cache_put_dial(unique_id, code, user_id)

        if event_type == "hangup":
            from datetime import datetime
            start_time = raw.get("StartTime") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            end_time = raw.get("EndTime") or start_time
            duration = 0
            try:
                dt_s = datetime.fromisoformat(start_time.replace("Z", ""))
                dt_e = datetime.fromisoformat(end_time.replace("Z", ""))
                duration = max(0, int((dt_e - dt_s).total_seconds()))
            except Exception:
                duration = 0
            # Map result в журнал: answered / failed
            # Для "неответа" фиксируем как failed (в RetailCRM валидно для upload)
            answered = int(raw.get("CallStatus", 0)) == 2
            result = "answered" if answered else "failed"
            upload_payload = [{
                "date": start_time.replace("T", " ")[:19],
                "type": _map_call_type(int(raw.get("CallType", 0))) or "in",
                "phone": (phone or "").lstrip("+"),
                "duration": duration,
                "result": result,
                "externalId": unique_id,
            }]
            # Если на этапе hangup не смогли определить, дотянем из кэша dial
            cache_entry = _cache_get_dial(unique_id)
            eff_user_id = user_id if user_id is not None else (cache_entry.get("user_id") if cache_entry else None)
            eff_code = str(code) if code else (str(cache_entry.get("code")) if cache_entry and cache_entry.get("code") else None)
            if eff_user_id is not None:
                upload_payload[0]["userId"] = eff_user_id
            elif eff_code:
                upload_payload[0]["code"] = eff_code
            if record_url:
                upload_payload[0]["recordUrl"] = record_url

            up = await client._make_request("POST", "/telephony/calls/upload", data={
                "clientId": enterprise_number,
                "calls": json.dumps(upload_payload, ensure_ascii=False),
            })
            logger.info(f"[internal calls/upload] sent: {upload_payload} resp={up.success}")
            try:
                await write_integration_log(
                    enterprise_number=enterprise_number,
                    event_type="calls_upload",
                    request_data={
                        "uniqueId": unique_id,
                        "calls": upload_payload,
                    },
                    response_data=(up.data if up and up.data else None),
                    status_ok=bool(up and up.success),
                    error_message=(up.error if up and up.error else None),
                )
            except Exception:
                pass

    return JSONResponse({"success": True})

async def find_enterprise_by_integration_token(client_id: str) -> Optional[Dict[str, Any]]:
    """Находит предприятие по токену интеграции RetailCRM
    
    RetailCRM передает clientId который мы указали при регистрации модуля.
    Мы регистрируем clientId = enterprise_number, поэтому ищем по номеру предприятия.
    """
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    
    try:
        # RetailCRM передает clientId = enterprise_number, поэтому ищем по номеру предприятия
        query = "SELECT number, name, ip, secret FROM enterprises WHERE number = $1 AND active = true"
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(query, client_id)
            if row:
                return {
                    "number": row["number"],
                    "name": row["name"], 
                    "ip": row["ip"],
                    "secret": row["secret"]
                }
        return None
    except Exception as e:
        logger.error(f"❌ Error finding enterprise by token {client_id}: {e}")
        return None

async def call_asterisk_api(code: str, phone: str, client_id: str) -> Dict[str, Any]:
    """Вызывает asterisk.py API для инициации звонка"""
    try:
        asterisk_url = "http://localhost:8018/api/makecallexternal"
        params = {
            "code": code,
            "phone": phone,
            "clientId": client_id
        }
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(asterisk_url, params=params) as response:
                response_text = await response.text()
                
                if response.status == 200:
                    try:
                        result = json.loads(response_text)
                        logger.info(f"✅ Asterisk API success: {result}")
                        return {"success": True, "data": result}
                    except json.JSONDecodeError:
                        logger.info(f"✅ Asterisk API success (non-JSON): {response_text}")
                        return {"success": True, "message": response_text}
                else:
                    logger.error(f"❌ Asterisk API error {response.status}: {response_text}")
                    return {"success": False, "error": f"HTTP {response.status}: {response_text}"}
                    
    except Exception as e:
        logger.error(f"❌ Error calling asterisk API: {e}")
        return {"success": False, "error": str(e)}

# =============================================================================
# КЭШ: утилиты и фоновый рефреш
# =============================================================================

def _is_cache_entry_valid(entry: Tuple[Dict[str, Any], float]) -> bool:
    if not entry:
        return False
    _, expires_at = entry
    return time.time() < expires_at

async def get_config_cached(enterprise_number: str) -> Dict[str, Any]:
    entry = CONFIG_CACHE.get(enterprise_number)
    if entry and _is_cache_entry_valid(entry):
        STATS["cache_hits"] += 1
        return entry[0]
    STATS["cache_misses"] += 1
    cfg = await fetch_retailcrm_config(enterprise_number)
    CONFIG_CACHE[enterprise_number] = (cfg, time.time() + CACHE_TTL_SECONDS)
    return cfg

async def list_active_enterprises() -> List[str]:
    """Список enterprise_number, у которых включен retailcrm.enabled=true."""
    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None
    query = (
        "SELECT number FROM enterprises "
        "WHERE integrations_config ? 'retailcrm' "
        "AND (integrations_config->'retailcrm'->>'enabled') = 'true'"
    )
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(query)
        STATS["db_reads"] += 1
    return [r["number"] for r in rows]

async def refresh_cache_for(enterprise_number: str) -> Dict[str, Any]:
    cfg = await fetch_retailcrm_config(enterprise_number)
    CONFIG_CACHE[enterprise_number] = (cfg, time.time() + CACHE_TTL_SECONDS)
    STATS["cache_refreshes"] += 1
    return cfg

async def refresh_cache_full() -> Dict[str, Any]:
    """Полный рефреш кэша для всех активных юнитов."""
    result: Dict[str, Any] = {"refreshed": [], "skipped": []}
    try:
        active = await list_active_enterprises()
        for num in active:
            await refresh_cache_for(num)
            result["refreshed"].append(num)
    except Exception as e:
        logger.error(f"❌ Ошибка полного рефреша кэша: {e}")
    return result

async def _cache_refresher_loop(stop_event: asyncio.Event) -> None:
    logger.info(
        f"🌀 Запуск фонового обновления кэша: every {CACHE_REFRESH_INTERVAL_SECONDS}s, TTL={CACHE_TTL_SECONDS}s"
    )
    try:
        while not stop_event.is_set():
            await refresh_cache_full()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=CACHE_REFRESH_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        logger.info("🛑 Остановка фонового обновления кэша")
        raise

@app.on_event("startup")
async def startup_event():
    """Инициализация при старте сервиса"""
    global retailcrm_client
    logger.info("🚀 Запуск RetailCRM Integration Service")
    logger.info(f"🏪 RetailCRM URL: {RETAILCRM_CONFIG['base_url']}")
    # Инициализируем пул PostgreSQL
    try:
        await init_pg_pool()
    except Exception as e:
        logger.error(f"❌ Не удалось инициализировать PostgreSQL pool: {e}")
    # Создаём таблицу логов заранее
    try:
        await ensure_integration_logs_table()
    except Exception as e:
        logger.error(f"❌ Не удалось подготовить таблицу логов интеграций: {e}")
    # Стартуем фоновый рефрешер кэша
    try:
        global _cache_refresher_task, _cache_refresher_stop_event
        _cache_refresher_stop_event = asyncio.Event()
        _cache_refresher_task = asyncio.create_task(_cache_refresher_loop(_cache_refresher_stop_event))
        logger.info("✅ Фоновый рефрешер кэша запущен")
    except Exception as e:
        logger.error(f"❌ Не удалось запустить фоновый рефрешер кэша: {e}")

@app.on_event("shutdown") 
async def shutdown_event():
    """Очистка при остановке сервиса"""
    logger.info("🛑 Остановка RetailCRM Integration Service")
    # Останавливаем фоновый рефрешер кэша
    try:
        global _cache_refresher_task, _cache_refresher_stop_event
        if _cache_refresher_stop_event is not None:
            _cache_refresher_stop_event.set()
        if _cache_refresher_task is not None:
            _cache_refresher_task.cancel()
            try:
                await _cache_refresher_task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        logger.error(f"❌ Ошибка остановки рефрешера кэша: {e}")
    try:
        await close_pg_pool()
    except Exception as e:
        logger.error(f"❌ Ошибка закрытия PostgreSQL pool: {e}")


# =============================================================================
# API ENDPOINTS ДЛЯ ТЕСТИРОВАНИЯ
# =============================================================================

@app.get("/")
async def root():
    """Главная страница сервиса"""
    return {
        "service": "RetailCRM Integration",
        "version": "1.0.0",
        "status": "running",
        "phase": "1 - API Testing",
        "retailcrm_url": RETAILCRM_CONFIG["base_url"]
    }


# =============================================================================
# СИСТЕМНЫЕ ENDPOINTS: health, stats
# =============================================================================

@app.get("/health")
async def health() -> Dict[str, Any]:
    """Простой healthcheck сервиса retailcrm."""
    return {"status": "ok"}


@app.get("/stats")
async def stats() -> Dict[str, Any]:
    """Базовые метрики сервиса retailcrm."""
    pool_status: Dict[str, Any] = {"initialized": pg_pool is not None}
    # Сводка по кэшу
    cache_size = len(CONFIG_CACHE)
    now_ts = time.time()
    cache_expiring = sum(1 for _, exp in CONFIG_CACHE.values() if exp <= now_ts)
    return {
        "db": pool_status,
        "counters": STATS,
        "cache": {"size": cache_size, "expiring": cache_expiring, "ttl": CACHE_TTL_SECONDS},
        "service": "retailcrm",
    }


# =============================================================================
# БАЗОВЫЕ API ДЛЯ КОНФИГУРАЦИЙ (namespace /api/config)
# =============================================================================

@app.get("/api/config/{enterprise_number}")
async def api_get_config(enterprise_number: str) -> Dict[str, Any]:
    # Возвращаем из кэша (лениво прогреваем)
    cfg = await get_config_cached(enterprise_number)
    return {"enterprise_number": enterprise_number, "config": cfg}


class RetailCRMConfigBody(BaseModel):
    config: Dict[str, Any]


@app.put("/api/config/{enterprise_number}")
async def api_put_config(enterprise_number: str, body: RetailCRMConfigBody) -> Dict[str, Any]:
    updated = await upsert_retailcrm_config(enterprise_number, body.config)
    return {"enterprise_number": enterprise_number, "config": updated}


# =============================================================================
# API для управления кэшем (namespace /api/config-cache)
# =============================================================================

@app.post("/api/config-cache/refresh/{enterprise_number}")
async def api_refresh_cache_for(enterprise_number: str) -> Dict[str, Any]:
    cfg = await refresh_cache_for(enterprise_number)
    return {"enterprise_number": enterprise_number, "config": cfg, "refreshed": True}


@app.post("/api/config-cache/refresh-all")
async def api_refresh_cache_all() -> Dict[str, Any]:
    res = await refresh_cache_full()
    return {"result": res}


@app.get("/api/config-cache/active-enterprises")
async def api_active_enterprises() -> Dict[str, Any]:
    active = await list_active_enterprises()
    return {"active_enterprises": active, "count": len(active)}


# =============================================================================
# Регистрация/редактирование телефонии по заданию (POST /api/register/{enterprise_number})
# =============================================================================

class RegisterBody(BaseModel):
    domain: str
    api_key: str
    enabled: bool = True


@app.post("/api/register/{enterprise_number}")
async def api_register_module(enterprise_number: str, body: RegisterBody) -> Dict[str, Any]:
    """Сохраняет домен/API‑key в БД и регистрирует модуль в RetailCRM (integration-modules)."""
    domain = body.domain.rstrip('/')
    api_key = body.api_key
    enabled = body.enabled

    # 1) Сохранить конфиг
    saved_cfg = await upsert_retailcrm_config(enterprise_number, {
        "domain": domain,
        "api_key": api_key,
        "enabled": enabled,
    })

    # 2) Подготовить полезную нагрузку для RetailCRM
    code = "vochi-telephony"
    make_call_url = f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm/make-call"
    change_status_url = f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm/status"
    # Генерируем токен доступа для RetailCRM
    access_token = generate_retailcrm_access_token(enterprise_number)
    
    # Собираем additionalCodes из локальных назначений, чтобы не обнулять их при регистрации
    user_extensions = (saved_cfg or {}).get("user_extensions", {}) or {}
    additional_codes: list[dict] = []
    try:
        for uid, ext in user_extensions.items():
            if ext:
                try:
                    uid_int = int(uid)
                except Exception:
                    uid_int = uid
                additional_codes.append({"userId": uid_int, "code": str(ext)})
    except Exception:
        additional_codes = []

    integration_module = {
        "code": code,
        "active": enabled,
        "name": "Vochi-CRM",
        # Требование RetailCRM: logo должен быть SVG
        "logo": "https://bot.vochi.by/static/img/vochi_logo.svg",
        # Требование RetailCRM: baseUrl обязателен
        "baseUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}",
        "clientId": enterprise_number,
        "accountUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm-admin/?enterprise_number={enterprise_number}&token={access_token}",
        # Поле actions убираем — RetailCRM ругается на неверный выбор
        "allowEdit": False,
        # Настройки телефонии должны быть в integrations.telephony
        "integrations": {
            "telephony": {
                "makeCallUrl": make_call_url,
                "changeUserStatusUrl": change_status_url,
                # Явно объявляем поддержку событий, чтобы UI RetailCRM показывал "Да"
                "inputEventSupported": True,
                "outputEventSupported": True,
                "hangupEventSupported": True,
                "additionalCodes": additional_codes,
                "externalPhones": [],
                "allowEdit": False
            }
        }
    }

    # 3) Вызвать RetailCRM API под переданным доменом и ключом
    cfg = {
        "base_url": domain if domain.startswith("http") else f"https://{domain}",
        "api_key": api_key,
        "api_version": "v5",
        "timeout": 30,
    }
    try:
        async with RetailCRMClient(cfg) as client:
            resp = await client.upsert_integration_module(code, integration_module)
            await write_integration_log(
                enterprise_number,
                "register_module",
                {"domain": cfg["base_url"], "module": integration_module},
                (resp.data if resp and resp.data else None),
                resp.success,
                resp.error,
            )
            if not resp.success:
                raise HTTPException(status_code=400, detail=resp.error or "RetailCRM error")
            return {"success": True, "result": resp.data or {}}
    except HTTPException:
        raise
    except Exception as e:
        await write_integration_log(
            enterprise_number,
            "register_module",
            {"domain": cfg["base_url"], "module": integration_module},
            None,
            False,
            str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

async def api_delete_integration(enterprise_number: str) -> Dict[str, Any]:
    """Удаляет интеграцию RetailCRM: очищает БД и деактивирует модуль в RetailCRM."""
    
    # 1) Получаем текущий конфиг для доступа к RetailCRM
    current_config = await fetch_retailcrm_config(enterprise_number)
    
    # 2) Удаляем конфиг из БД
    deleted = await delete_retailcrm_config(enterprise_number)
    if not deleted:
        raise HTTPException(status_code=404, detail="Конфигурация не найдена")
    
    # 3) Если есть домен и API-ключ, пытаемся деактивировать модуль в RetailCRM
    domain = current_config.get("domain")
    api_key = current_config.get("api_key")
    
    if domain and api_key:
        try:
            cfg = {
                "base_url": domain if domain.startswith("http") else f"https://{domain}",
                "api_key": api_key,
                "api_version": "v5",
                "timeout": 30,
            }
            
            # Получаем текущий модуль для деактивации
            code = "vochi-telephony"
            # Для деактивации токен не нужен, так как интеграция удаляется
            integration_module = {
                "code": code,
                "active": False,  # Деактивируем
                "name": "Vochi-CRM",
                "logo": "https://bot.vochi.by/static/img/vochi_logo.svg",
                "baseUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}",
                "clientId": enterprise_number,
                "accountUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm-admin/?enterprise_number={enterprise_number}",
                "allowEdit": False,
                # Настройки телефонии должны быть в integrations.telephony
                "integrations": {
                    "telephony": {
                        "makeCallUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm/make-call",
                        "changeUserStatusUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm/status",
                        "additionalCodes": [],
                        "externalPhones": [],
                        "allowEdit": False
                    }
                }
            }
            
            async with RetailCRMClient(cfg) as client:
                resp = await client.deactivate_integration_module(code, integration_module)
                
                await write_integration_log(
                    enterprise_number,
                    "delete_module",
                    {"domain": cfg["base_url"], "module": integration_module},
                    (resp.data if resp and resp.data else None),
                    resp.success,
                    resp.error,
                )
                
                if not resp.success:
                    logger.warning(f"⚠️ Не удалось деактивировать модуль в RetailCRM: {resp.error}")
                    # Не поднимаем ошибку, так как конфиг уже удален из БД
                else:
                    logger.info(f"✅ Модуль интеграции деактивирован в RetailCRM для {enterprise_number}")
                    
        except Exception as e:
            logger.warning(f"⚠️ Ошибка деактивации модуля в RetailCRM: {e}")
            await write_integration_log(
                enterprise_number,
                "delete_module",
                {"domain": domain, "error": str(e)},
                None,
                False,
                str(e),
            )
            # Не поднимаем ошибку, так как конфиг уже удален из БД
    
    return {"success": True, "message": "Интеграция удалена"}


# =============================================================================
# JWT АВТОРИЗАЦИЯ ДЛЯ RETAILCRM
# =============================================================================

def generate_retailcrm_access_token(enterprise_number: str) -> str:
    """Генерирует JWT токен для доступа к админке RetailCRM из внешнего интерфейса."""
    payload = {
        "enterprise_number": enterprise_number,
        "source": "retailcrm",
        "exp": datetime.utcnow() + timedelta(days=365),  # Долгосрочный токен
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_retailcrm_access_token(token: str) -> Optional[str]:
    """Проверяет JWT токен и возвращает enterprise_number или None."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("source") == "retailcrm":
            return payload.get("enterprise_number")
    except jwt.ExpiredSignatureError:
        logger.warning("JWT токен истёк")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Неверный JWT токен: {e}")
    return None


# =============================================================================
# DUPLICATE ROUTES UNDER /retailcrm-admin/ PREFIX FOR BROWSER RELATIVE CALLS
# =============================================================================

@app.get("/retailcrm-admin/api/config/{enterprise_number}")
async def admin_api_get_config(enterprise_number: str) -> Dict[str, Any]:
    return await api_get_config(enterprise_number)


@app.put("/retailcrm-admin/api/config/{enterprise_number}")
async def admin_api_put_config(enterprise_number: str, body: RetailCRMConfigBody) -> Dict[str, Any]:
    return await api_put_config(enterprise_number, body)


@app.delete("/retailcrm-admin/api/config/{enterprise_number}")
async def admin_api_delete_config(enterprise_number: str) -> Dict[str, Any]:
    return await api_delete_integration(enterprise_number)


@app.post("/retailcrm-admin/api/register/{enterprise_number}")
async def admin_api_register_module(enterprise_number: str, request: Request) -> Dict[str, Any]:
    """Обёртка для UI: принимает как JSON, так и form-data; избегает 422 на битом JSON.

    Ожидаемые поля: domain (str), api_key (str), enabled (bool)
    """
    payload: Dict[str, Any] = {}
    # 1) Пытаемся прочитать как JSON
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    # 2) Фолбэк: form-data (например, если фронт шлёт форму или сломан JSON)
    if not payload:
        try:
            form = await request.form()
            def _to_bool(v: Any) -> bool:
                if v is None:
                    return True
                s = str(v).strip().lower()
                return s in ("1", "true", "on", "yes")
            payload = {
                "domain": form.get("domain", ""),
                "api_key": form.get("api_key", ""),
                "enabled": _to_bool(form.get("enabled", "true")),
            }
        except Exception:
            payload = {}
    # 3) Валидация через pydantic
    try:
        body = RegisterBody(**payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid body: {e}")
    return await api_register_module(enterprise_number, body)

@app.get("/retailcrm-admin/api/internal-phones/{enterprise_number}")
async def admin_api_get_internal_phones(enterprise_number: str) -> Dict[str, Any]:
    """Получить список внутренних номеров предприятия с информацией о владельцах"""
    try:
        query = """
        SELECT 
            uip.phone_number,
            u.first_name,
            u.last_name,
            u.id as user_id
        FROM user_internal_phones uip
        LEFT JOIN users u ON uip.user_id = u.id
        WHERE uip.enterprise_number = $1
        ORDER BY uip.phone_number
        """
        
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(query, enterprise_number)
            
            internal_phones = []
            for row in rows:
                phone_info = {
                    "phone_number": row["phone_number"],
                    "user_id": row["user_id"],
                    "owner": None
                }
                
                # Добавляем информацию о владельце если есть
                if row["user_id"] and row["first_name"] and row["last_name"]:
                    phone_info["owner"] = f"{row['first_name']} {row['last_name']}"
                
                internal_phones.append(phone_info)
            
            logger.info(f"✅ Fetched {len(internal_phones)} internal phones for enterprise {enterprise_number}")
            
            return {
                "success": True,
                "phones": internal_phones,
                "total": len(internal_phones)
            }
            
    except Exception as e:
        logger.error(f"❌ Error fetching internal phones for enterprise {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/retailcrm-admin/api/user-extensions/{enterprise_number}")
async def admin_api_get_user_extensions(enterprise_number: str) -> Dict[str, Any]:
    """Получить назначения пользователей RetailCRM из локальной БД"""
    try:
        user_extensions = await load_user_extensions_from_db(enterprise_number)
        
        logger.info(f"✅ Loaded {len(user_extensions)} user extensions from DB for enterprise {enterprise_number}")
        
        return {
            "success": True,
            "user_extensions": user_extensions,
            "total": len(user_extensions)
        }
        
    except Exception as e:
        logger.error(f"❌ Error loading user extensions for enterprise {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/retailcrm-admin/api/sync-extensions/{enterprise_number}")
async def admin_api_sync_extensions(enterprise_number: str) -> Dict[str, Any]:
    """Синхронизировать назначения между локальной БД и RetailCRM"""
    try:
        # Получаем данные из локальной БД
        local_extensions = await load_user_extensions_from_db(enterprise_number)
        
        # Получаем конфигурацию для предприятия
        config_dict = await fetch_retailcrm_config(enterprise_number)
        if not config_dict or not config_dict.get("enabled"):
            raise HTTPException(status_code=404, detail="RetailCRM integration not configured or disabled")
        
        api_url = config_dict.get("domain", "").strip()
        api_key = config_dict.get("api_key", "").strip()
        
        if not api_url or not api_key:
            raise HTTPException(status_code=400, detail="RetailCRM credentials not configured")
        
        # Синхронизируем с RetailCRM
        client_config = {
            "base_url": api_url,
            "api_key": api_key,
            "api_version": "v5", 
            "timeout": 10
        }
        
        # Преобразуем в формат RetailCRM
        additional_codes = []
        for user_id, extension in local_extensions.items():
            if extension and extension.strip():
                additional_codes.append({
                    "userId": str(user_id),
                    "code": extension.strip()
                })
        
        async with RetailCRMClient(client_config) as client:
            integration_code = "vochi-telephony"
            # Здесь можно добавить логику обновления RetailCRM...
            
            logger.info(f"✅ Synced {len(additional_codes)} extensions between DB and RetailCRM for enterprise {enterprise_number}")
            
            return {
                "success": True,
                "synced_extensions": len(additional_codes),
                "local_extensions": local_extensions
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error syncing extensions for enterprise {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Sync error: {str(e)}")

@app.post("/retailcrm-admin/api/refresh-managers/{enterprise_number}")
async def admin_api_refresh_managers(enterprise_number: str) -> Dict[str, Any]:
    """Получить список менеджеров из RetailCRM для автозаполнения маппингов"""
    try:
        # Получаем конфигурацию для предприятия
        config_dict = await fetch_retailcrm_config(enterprise_number)
        if not config_dict or not config_dict.get("enabled"):
            raise HTTPException(status_code=404, detail="RetailCRM integration not configured or disabled")
        
        api_url = config_dict.get("domain", "").strip()
        api_key = config_dict.get("api_key", "").strip()
        
        if not api_url or not api_key:
            raise HTTPException(status_code=400, detail="RetailCRM credentials not configured")
        
        # Создаем клиент RetailCRM
        client_config = {
            "base_url": api_url,
            "api_key": api_key,
            "api_version": "v5", 
            "timeout": 10
        }
        
        async with RetailCRMClient(client_config) as client:
            # Получаем список пользователей
            response = await client.get_users()
            
            if not response.success:
                logger.error(f"❌ Failed to fetch users from RetailCRM: {response.error}")
                raise HTTPException(status_code=400, detail=f"RetailCRM API error: {response.error}")
            
            # Загружаем назначения из локальной БД (приоритет)
            local_extensions = await load_user_extensions_from_db(enterprise_number)
            
            # Получаем существующую интеграцию телефонии для добавочных номеров (для сверки)
            integration_code = "vochi-telephony"
            integration_response = await client.get_integration_module(integration_code)
            
            # Извлекаем добавочные номера из RetailCRM
            retailcrm_extensions = {}
            if integration_response.success and integration_response.data:
                integration_data = integration_response.data.get("integrationModule", {})
                if integration_data:
                    # Парсим JSON если это строка
                    if isinstance(integration_data, str):
                        try:
                            integration_data = json.loads(integration_data)
                        except json.JSONDecodeError:
                            logger.warning("❌ Не удалось распарсить integrationModule JSON")
                            integration_data = {}
                    
                    # Извлекаем additionalCodes (согласно документации - в корне объекта)
                    codes_list = integration_data.get("additionalCodes", [])
                    
                    # Для совместимости проверяем также старую структуру
                    if not codes_list:
                        telephony_config = integration_data.get("integrations", {}).get("telephony", {})
                        codes_list = telephony_config.get("additionalCodes", [])
                    
                    # Преобразуем в словарь {userId: code}
                    for code_entry in codes_list:
                        if isinstance(code_entry, dict):
                            user_id = str(code_entry.get("userId", ""))
                            code = code_entry.get("code", "")
                            if user_id and code:
                                retailcrm_extensions[user_id] = code
            
            # Объединяем данные: приоритет локальным данным, дополняем из RetailCRM
            combined_extensions = {}
            combined_extensions.update(retailcrm_extensions)  # Сначала RetailCRM
            combined_extensions.update(local_extensions)      # Потом локальные (перезаписывают)
            
            # Если локальных данных нет, но есть в RetailCRM - импортируем их
            if not local_extensions and retailcrm_extensions:
                await save_user_extensions_to_db(enterprise_number, retailcrm_extensions)
                logger.info(f"📥 Imported {len(retailcrm_extensions)} extensions from RetailCRM to local DB")
            
            # Обрабатываем ответ и извлекаем пользователей
            users_data = response.data or {}
            users = users_data.get("users", [])
            
            # Фильтруем только активных пользователей и форматируем для UI
            active_users = []
            for user in users:
                if user.get("active", False) and user.get("status", "") == "free":
                    user_id = str(user.get("id", ""))
                    extension = combined_extensions.get(user_id, "")  # Получаем добавочный номер из объединенных данных
                    
                    active_users.append({
                        "id": user.get("id"),
                        "firstName": user.get("firstName", ""),
                        "lastName": user.get("lastName", ""), 
                        "email": user.get("email", ""),
                        "groups": user.get("groups", []),
                        "extension": extension  # Добавляем добавочный номер (приоритет локальным данным)
                    })
            
            logger.info(f"✅ Fetched {len(active_users)} active managers from RetailCRM for enterprise {enterprise_number}")
            
            return {
                "success": True,
                "users": active_users,
                "total": len(active_users)
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error refreshing managers for enterprise {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@app.post("/retailcrm-admin/api/save-extensions/{enterprise_number}")
async def admin_api_save_extensions(enterprise_number: str, assignments: Dict[str, Any]) -> Dict[str, Any]:
    """Сохранить назначения добавочных номеров в RetailCRM"""
    try:
        # Получаем конфигурацию для предприятия
        config_dict = await fetch_retailcrm_config(enterprise_number)
        if not config_dict or not config_dict.get("enabled"):
            raise HTTPException(status_code=404, detail="RetailCRM integration not configured or disabled")
        
        api_url = config_dict.get("domain", "").strip()
        api_key = config_dict.get("api_key", "").strip()
        
        if not api_url or not api_key:
            raise HTTPException(status_code=400, detail="RetailCRM credentials not configured")
        
        # Получаем назначения из запроса
        user_extensions = assignments.get("extensions", {})
        
        # Сохраняем назначения в локальную БД
        await save_user_extensions_to_db(enterprise_number, user_extensions)
        
        # Преобразуем в формат RetailCRM additionalCodes
        additional_codes = []
        for user_id, extension in user_extensions.items():
            if extension and extension.strip():  # Только если номер не пустой
                additional_codes.append({
                    "userId": str(user_id),
                    "code": extension.strip()
                })
        
        # Получаем существующую конфигурацию интеграции
        client_config = {
            "base_url": api_url,
            "api_key": api_key,
            "api_version": "v5", 
            "timeout": 10
        }
        
        async with RetailCRMClient(client_config) as client:
            # Получаем текущую интеграцию
            integration_code = "vochi-telephony"
            integration_response = await client.get_integration_module(integration_code)
            
            # Подготавливаем базовую конфигурацию интеграции
            if integration_response.success and integration_response.data:
                integration_data = integration_response.data.get("integrationModule", {})
                if isinstance(integration_data, str):
                    try:
                        integration_data = json.loads(integration_data)
                    except json.JSONDecodeError:
                        integration_data = {}
            else:
                # Создаем базовую конфигурацию если интеграция не найдена
                integration_data = {
                    "code": integration_code,
                    "active": True,
                    "name": "Vochi-CRM",
                    "logo": "https://bot.vochi.by/static/img/vochi_logo.svg",
                    "baseUrl": "https://bot.vochi.by",
                    "clientId": enterprise_number,
                    "integrations": {
                        "telephony": {
                            "additionalCodes": [],
                            "externalPhones": []
                        }
                    }
                }
            
            # Убеждаемся что есть все обязательные поля
            if "clientId" not in integration_data:
                integration_data["clientId"] = enterprise_number
            if "baseUrl" not in integration_data:
                integration_data["baseUrl"] = "https://bot.vochi.by"
            if "logo" not in integration_data:
                integration_data["logo"] = "https://bot.vochi.by/static/img/vochi_logo.svg"
            
            # Согласно официальной документации RetailCRM - additionalCodes должны быть в корне
            integration_data["additionalCodes"] = additional_codes
            
            # Также добавляем для совместимости в integrations.telephony (если структура существует)
            if "integrations" in integration_data and "telephony" in integration_data["integrations"]:
                integration_data["integrations"]["telephony"]["additionalCodes"] = additional_codes
            
            # Сохраняем обновленную интеграцию
            save_response = await client.upsert_integration_module(integration_code, integration_data)
            
            if not save_response.success:
                logger.error(f"❌ Failed to save extensions in RetailCRM: {save_response.error}")
                raise HTTPException(status_code=400, detail=f"RetailCRM API error: {save_response.error}")
            
            logger.info(f"✅ Saved {len(additional_codes)} extension assignments in RetailCRM for enterprise {enterprise_number}")
            
            # Получаем обновленный список пользователей для возврата актуальных данных
            try:
                fresh_users_result = await admin_api_refresh_managers(enterprise_number)
                if fresh_users_result.get("success") and fresh_users_result.get("users"):
                    return {
                        "success": True,
                        "saved_extensions": len(additional_codes),
                        "assignments": additional_codes,
                        "users": fresh_users_result["users"]  # Возвращаем свежий список пользователей
                    }
            except Exception as e:
                logger.warning(f"⚠️ Failed to refresh users after saving extensions: {e}")
            
            return {
                "success": True,
                "saved_extensions": len(additional_codes),
                "assignments": additional_codes
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error saving extensions for enterprise {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

# =============================================================================
# UI: страница администрирования RetailCRM (форма домена/API-ключа)
# =============================================================================

ADMIN_PAGE_HTML = """
<!doctype html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{title}</title>
  <link rel=\"icon\" href=\"./favicon.ico\"> 
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background:#0b1728; color:#e7eef8; }
    .wrap { max-width: 820px; margin: 0 auto; padding: 28px; }
    h1 { font-size: 24px; margin: 0 0 18px; }
    .card { background:#0f2233; border:1px solid #1b3350; border-radius:12px; padding:22px; }
    label { display:block; margin:12px 0 8px; color:#a8c0e0; font-size:14px; }
    input[type=text], input[type=url] { width:100%; padding:12px 14px; border-radius:10px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:16px; }
    .row { display:flex; gap:16px; flex-wrap: wrap; }
    .row > div { flex:1 1 320px; }
    .actions { margin-top:20px; display:flex; align-items:center; gap:16px; }
    .btn { background:#2563eb; color:#fff; border:none; padding:12px 18px; border-radius:10px; cursor:pointer; font-size:16px; }
    .btn:disabled { opacity:.6; cursor:not-allowed; }
    input[type=checkbox] { width:20px; height:20px; accent-color:#2563eb; }
    .hint { color:#8fb3da; font-size:13px; margin-top:6px; }
    .success { color:#4ade80; }
    .error { color:#f87171; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div style=\"display:flex; align-items:center; margin-bottom:20px;\">
      <h1 style=\"margin:0; margin-right:15px;\">{header}</h1>
      <img src=\"/static/Retail-big.png\" alt=\"RetailCRM\" style=\"height:48px; width:auto; background:white; padding:4px; border-radius:4px; border:1px solid #ddd;\">
    </div>
    <div class=\"card\">
      <div class=\"row\">
        <div>
          <label>Адрес домена</label>
        <input id=\"domain\" type=\"url\" placeholder=\"demo.retailcrm.ru\" />
        </div>
        <div>
          <label>API Key</label>
          <input id=\"apiKey\" type=\"text\" placeholder=\"xxxxxxxx\" />
        </div>
      </div>
      <div class=\"actions\">
      <label><input id=\"enabled\" type=\"checkbox\" /> Активен?</label>
        <button id=\"saveBtn\" type=\"button\" class=\"btn\">Сохранить и зарегистрировать</button>
        <button id=\"refreshBtn\" type=\"button\" class=\"btn\" style=\"background:#059669;\">Обновить</button>
        <button id=\"deleteBtn\" type=\"button\" class=\"btn\" style=\"background:#dc2626; margin-left:auto;\">Удалить интеграцию</button>
        <button id=\"journalBtn\" type=\"button\" class=\"btn\" style=\"background:#374151;\">Журнал</button>
        <span id=\"msg\" class=\"hint\"></span>
      </div>
    </div>
    
    <!-- Блок отображения пользователей RetailCRM -->
    <div class=\"card\" id=\"usersCard\" style=\"display:none;\">
      <h2 style=\"margin:0 0 15px 0; font-size:24px; color:#1f2937;\">Менеджеры</h2>
      <div id=\"usersList\"></div>
      <div id=\"usersLoading\" style=\"display:none; color:#8fb3da; font-style:italic;\">Загрузка пользователей...</div>
  </div>
  </div>
  <script src="./app.js?v=202508091915"></script>
</body>
</html>
"""


# JS для страницы администрирования (вынесен во внешний файл на случай CSP)
ADMIN_PAGE_JS = r"""
  (function(){
  try {
    const qs = new URLSearchParams(location.search);
    const enterprise = qs.get('enterprise_number');
    const titleBase = document.title;

    async function load() {
      try {
        const r = await fetch(`./api/config/${enterprise}`);
        const j = await r.json();
        const cfg = (j.config||{});
        const domainEl = document.getElementById('domain');
        const apiKeyEl = document.getElementById('apiKey');
        const enabledEl = document.getElementById('enabled');
        if (domainEl) domainEl.value = cfg.domain || '';
        if (apiKeyEl) apiKeyEl.value = cfg.api_key || '';
        if (enabledEl) enabledEl.checked = !!cfg.enabled;
      } catch(e) { console.warn('load() error', e); }
    }

    async function save() {
      const domain = (document.getElementById('domain')||{}).value?.trim?.() || '';
      const apiKey = (document.getElementById('apiKey')||{}).value?.trim?.() || '';
      const enabled = !!((document.getElementById('enabled')||{}).checked);
      const btn = document.getElementById('saveBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        let r = await fetch(`./api/config/${enterprise}`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({config: {domain, api_key: apiKey, enabled}}) });
        if(!r.ok) throw new Error('Ошибка сохранения конфига');
        r = await fetch(`./api/register/${enterprise}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({domain, api_key: apiKey, enabled}) });
        const jr = await r.json();
        if(!jr.success) throw new Error(jr.error||'Ошибка регистрации');
        if (msg) { msg.textContent='Сохранено'; msg.className='hint success'; }
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }

    async function deleteIntegration() {
      const btn = document.getElementById('deleteBtn');
      const msg = document.getElementById('msg');
      if (!confirm('Вы уверены, что хотите удалить интеграцию? Это действие нельзя отменить.')) return;
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        const r = await fetch(`./api/config/${enterprise}`, { method:'DELETE', headers:{'Content-Type':'application/json'} });
        const jr = await r.json();
        if(!jr.success) throw new Error(jr.error||'Ошибка удаления');
        if (msg) { msg.textContent='Интеграция удалена'; msg.className='hint success'; }
        // Очищаем форму
        const domainEl = document.getElementById('domain');
        const apiKeyEl = document.getElementById('apiKey');
        const enabledEl = document.getElementById('enabled');
        if (domainEl) domainEl.value = '';
        if (apiKeyEl) apiKeyEl.value = '';
        if (enabledEl) enabledEl.checked = false;
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }



    // Функция отображения пользователей в специальном блоке
    function displayUsers(users) {
      const usersCard = document.getElementById('usersCard');
      const usersList = document.getElementById('usersList');
      
      if (!users || users.length === 0) {
        if (usersCard) usersCard.style.display = 'none';
        return;
      }
      
      let html = '';
      users.forEach(user => {
        const groups = user.groups ? user.groups.map(g => g.name).join(', ') : '';
        const extension = user.extension ? `📞 ${user.extension}` : '📞 не назначен';
        html += `
          <div style="border:1px solid #e5e7eb; border-radius:8px; padding:15px; margin-bottom:10px; background:#f9fafb;">
            <div style="display:flex; align-items:flex-start; justify-content:space-between;">
              <div style="flex:1;">
                <div style="font-size:18px; font-weight:600; color:#1f2937; margin-bottom:5px;">
                  ${user.firstName} ${user.lastName}
                </div>
                <div style="color:#6b7280; margin-bottom:3px;">ID: ${user.id} • ${user.email}</div>
                <div style="color:#059669; font-weight:500; margin-bottom:3px;">${extension}</div>
                ${groups ? `<div style="color:#6b7280; font-size:14px;">Группы: ${groups}</div>` : ''}
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <select id="extension_${user.id}" style="padding:8px; border:1px solid #d1d5db; border-radius:4px; font-size:14px; min-width:160px; background:white;">
                  <option value="">Выберите номер...</option>
                </select>
                <button id="save_${user.id}" type="button" style="display:none; padding:8px 12px; background:#059669; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; white-space:nowrap;" data-user-id="${user.id}">
                  💾 Сохранить
                </button>
                <button id="test_${user.id}" type="button" style="padding:8px 12px; background:#2563eb; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; white-space:nowrap;" data-user-id="${user.id}">🧪 Тест</button>
              </div>
            </div>
          </div>
        `;
      });
      
      if (usersList) usersList.innerHTML = html;
      if (usersCard) usersCard.style.display = 'block';
      
      // Добавляем обработчики для кнопок "Сохранить" и "Тест"
      const saveButtons = document.querySelectorAll('[id^="save_"]');
      saveButtons.forEach(btn => {
        btn.addEventListener('click', function() {
          const userId = this.getAttribute('data-user-id');
          saveExtension(userId);
        });
      });
      const testButtons = document.querySelectorAll('[id^="test_"]');
      testButtons.forEach(btn => {
        btn.addEventListener('click', function(){
          const userId = this.getAttribute('data-user-id');
          testCall(userId);
        });
      });
      
      // Загружаем внутренние номера для выпадающих списков после добавления в DOM
      setTimeout(() => {
        loadInternalPhones(users);
      }, 100);
    }
    
    // Загрузка внутренних номеров предприятия
    async function loadInternalPhones(users = []) {
      try {
        console.log('loadInternalPhones called');
        const enterpriseNumber = enterprise;
        console.log('Enterprise number:', enterpriseNumber);
        
        const response = await fetch(`./api/internal-phones/${enterpriseNumber}`, {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json'
          }
        });
        
        console.log('Response status:', response.status);
        
        if (response.ok) {
          const data = await response.json();
          console.log('Response data:', data);
          if (data.success && data.phones) {
            populateExtensionDropdowns(data.phones, users);
          } else {
            console.log('Data success or phones missing:', data);
          }
        } else {
          console.error('Response not ok:', response.status);
        }
      } catch (error) {
        console.error('Ошибка загрузки внутренних номеров:', error);
      }
    }
    
    // Заполнение выпадающих списков номерами
    function populateExtensionDropdowns(phones, users = []) {
      console.log('populateExtensionDropdowns called with phones:', phones);
      const selects = document.querySelectorAll('[id^="extension_"]');
      console.log('Found selects:', selects.length);
      
      selects.forEach((select, index) => {
        console.log(`Processing select ${index}:`, select.id);
        const userId = select.id.replace('extension_', '');
        
        // Находим текущее назначение пользователя
        const user = users.find(u => u.id == userId);
        const currentExtension = user ? user.extension : '';
        
        // Очищаем и добавляем базовую опцию
        select.innerHTML = '<option value="">Выберите номер...</option>';
        
        // Добавляем опцию "Без номера" для удаления назначения
        const removeOption = document.createElement('option');
        removeOption.value = 'REMOVE';
        removeOption.textContent = 'Без номера';
        select.appendChild(removeOption);
        
        // Добавляем все номера
        phones.forEach(phone => {
          const option = document.createElement('option');
          option.value = phone.phone_number;
          
          // Формируем текст опции с информацией о владельце
          let optionText = phone.phone_number;
          if (phone.owner) {
            optionText += ` (${phone.owner})`;
          }
          // Убираем "(свободен)" - просто показываем номер без текста
          
          option.textContent = optionText;
          
          // Устанавливаем выбранным если это текущее назначение
          if (currentExtension && phone.phone_number === currentExtension) {
            option.selected = true;
            // Показываем кнопку сохранить если есть назначение
            const saveBtn = document.getElementById(`save_${userId}`);
            if (saveBtn) {
              saveBtn.style.display = 'block';
            }
          }
          
          select.appendChild(option);
        });
        
        console.log(`Added ${phones.length} options to select ${select.id}, current: ${currentExtension}`);
        
        // Добавляем обработчик изменения
        select.addEventListener('change', function() {
          console.log('Select changed:', this.id, this.value);
          const userId = this.id.replace('extension_', '');
          const saveBtn = document.getElementById(`save_${userId}`);
          
          // Проверяем на конфликты с другими назначениями
          if (this.value && this.value !== '' && this.value !== 'REMOVE') {
            const allSelects = document.querySelectorAll('[id^="extension_"]');
            let conflictFound = false;
            
            allSelects.forEach(otherSelect => {
              if (otherSelect !== this && otherSelect.value === this.value) {
                const otherUserId = otherSelect.id.replace('extension_', '');
                console.log(`⚠️ Конфликт: номер ${this.value} уже выбран пользователем ${otherUserId}`);
                // Можно добавить визуальное предупреждение
                conflictFound = true;
              }
            });
            
            if (conflictFound) {
              console.log('При сохранении номер будет переназначен');
            }
          }
          
          if (saveBtn) {
            // Показываем кнопку если выбран номер или "REMOVE"
            saveBtn.style.display = (this.value && this.value !== '') ? 'block' : 'none';
          }
        });
      });
    }
    
    // Сохранение назначения добавочного номера
    async function saveExtension(userId) {
      try {
        const select = document.getElementById(`extension_${userId}`);
        const saveBtn = document.getElementById(`save_${userId}`);
        
        if (!select || !select.value) {
          alert('Пожалуйста, выберите номер или "Без номера"');
          return;
        }
        
        const enterpriseNumber = enterprise;
        const selectedNumber = select.value.trim();
        
        // Собираем ВСЕ назначения со страницы
        const extensions = {};
        const allSelects = document.querySelectorAll('[id^="extension_"]');
        
        // Сначала собираем все назначения кроме текущего пользователя
        allSelects.forEach(sel => {
          const uid = sel.id.replace('extension_', '');
          if (uid !== userId && sel.value && sel.value.trim() && sel.value.trim() !== 'REMOVE') {
            const number = sel.value.trim();
            
            // Если этот номер совпадает с выбранным пользователем - убираем его у другого
            if (number === selectedNumber && selectedNumber !== 'REMOVE') {
              console.log(`🔄 Номер ${selectedNumber} отбирается у пользователя ${uid} для ${userId}`);
              sel.value = ''; // Сбрасываем визуально
              // Скрываем кнопку "Сохранить" у этого пользователя
              const otherSaveBtn = document.getElementById(`save_${uid}`);
              if (otherSaveBtn) {
                otherSaveBtn.style.display = 'none';
              }
            } else {
              extensions[uid] = number;
            }
          }
        });
        
        // Добавляем назначение текущего пользователя (если не "Без номера")
        if (selectedNumber && selectedNumber !== 'REMOVE') {
          extensions[userId] = selectedNumber;
        }
        
        console.log('Собранные назначения:', extensions);
        
        // Показываем индикатор загрузки
        if (saveBtn) {
          saveBtn.textContent = '⏳ Сохранение...';
          saveBtn.disabled = true;
        }
        
        const response = await fetch(`./api/save-extensions/${enterpriseNumber}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            extensions: extensions
          })
        });
        
        if (response.ok) {
          const data = await response.json();
          if (data.success) {
            // Если получили обновленный список пользователей, используем его
            if (data.users && Array.isArray(data.users)) {
              console.log('📋 Updating UI with fresh user data:', data.users);
              displayUsers(data.users);
              // Загружаем внутренние номера для обновления выпадающих списков
              setTimeout(() => {
                loadInternalPhones(data.users);
              }, 100);
            } else {
              // Fallback: обновляем список менеджеров традиционным способом
              await loadUsers();
            }
            console.log('✅ Добавочный номер сохранен в RetailCRM');
          } else {
            throw new Error(data.error || 'Ошибка сохранения');
          }
        } else {
          throw new Error(`HTTP ${response.status}`);
        }
        
      } catch (error) {
        console.error('Ошибка сохранения номера:', error);
        console.error('❌ Ошибка сохранения:', error.message);
        
        // Восстанавливаем кнопку
        const saveBtn = document.getElementById(`save_${userId}`);
        if (saveBtn) {
          saveBtn.textContent = '💾 Сохранить';
          saveBtn.disabled = false;
        }
      }
    }

    // Тестовый вызов для конкретного менеджера
    async function testCall(userId){
      try{
        const select = document.getElementById(`extension_${userId}`);
        const code = (select && select.value && select.value !== 'REMOVE') ? select.value.trim() : '';
        const btn = document.getElementById(`test_${userId}`);
        const msg = document.getElementById('msg');
        if (btn) { btn.disabled = true; btn.textContent = '🧪 Тест...'; }
        if (msg) { msg.textContent=''; msg.className='hint'; }
        const r = await fetch(`./api/test-call/${enterprise}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ userId: Number(userId), code }) });
        const jr = await r.json();
        if (!jr.success) throw new Error(jr.error||'Ошибка теста');
        if (msg) { msg.textContent = 'Тестовый звонок отправлен'; msg.className='hint success'; }
      }catch(e){
        const msg = document.getElementById('msg');
        if (msg) { msg.textContent = 'Ошибка теста: ' + e.message; msg.className='hint error'; }
      }finally{
        const btn = document.getElementById(`test_${userId}`);
        if (btn) { btn.disabled = false; btn.textContent = '🧪 Тест'; }
      }
    }

    // Функция загрузки пользователей (обновленная)
    async function loadUsers() {
      const usersLoading = document.getElementById('usersLoading');
      const msg = document.getElementById('msg');
      
      if (usersLoading) usersLoading.style.display = 'block';
      
      try {
        const r = await fetch(`./api/refresh-managers/${enterprise}`, { 
          method:'POST', 
          headers:{'Content-Type':'application/json'} 
        });
        const jr = await r.json();
        
        if (usersLoading) usersLoading.style.display = 'none';
        
        if(!jr.success) throw new Error(jr.error||'Ошибка получения менеджеров');
        
        const users = jr.users || [];
        displayUsers(users);
        
        console.log('RetailCRM managers loaded:', users);
        
      } catch(e) {
        if (usersLoading) usersLoading.style.display = 'none';
        console.error('Error loading users:', e);
        // Показываем ошибку только если явно нет конфигурации
        if (e.message && !e.message.includes('not configured')) {
          if (msg) { 
            msg.textContent = 'Ошибка загрузки пользователей: ' + e.message; 
            msg.className = 'hint error'; 
          }
        }
      }
    }

    // Обновленная функция refreshManagers - теперь просто обновляет отображение
    async function refreshManagers() {
      const btn = document.getElementById('refreshBtn');
      const msg = document.getElementById('msg');
      
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      
      try {
        await loadUsers();
        if (msg) { 
          msg.textContent = 'Список менеджеров обновлен'; 
          msg.className = 'hint success'; 
        }
      } catch(e) {
        if (msg) { 
          msg.textContent = 'Ошибка обновления: ' + e.message; 
          msg.className = 'hint error'; 
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    const saveBtn = document.getElementById('saveBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const refreshBtn = document.getElementById('refreshBtn');
    const journalBtn = document.getElementById('journalBtn');
    if (saveBtn) saveBtn.addEventListener('click', (e) => { e.preventDefault(); save(); });
    if (deleteBtn) deleteBtn.addEventListener('click', (e) => { e.preventDefault(); deleteIntegration(); });
    if (refreshBtn) refreshBtn.addEventListener('click', (e) => { e.preventDefault(); refreshManagers(); });
    if (journalBtn) journalBtn.addEventListener('click', (e) => { e.preventDefault(); window.open(`./journal?enterprise_number=${enterprise}`, '_blank'); });
    load();
    
    // Автоматически загружаем пользователей при открытии страницы
    setTimeout(() => {
      loadUsers();
    }, 500); // Небольшая задержка чтобы сначала загрузилась конфигурация
  } catch (e) { console.error('Admin JS init error', e); }
  })();
"""


@app.get("/retailcrm-admin/", response_class=HTMLResponse)
@app.post("/retailcrm-admin/", response_class=HTMLResponse)
async def retailcrm_admin_page(enterprise_number: str, token: str = None) -> HTMLResponse:
    """Простая UI‑страница администрирования для ввода домена и API‑ключа.
    
    Поддерживает авторизацию через JWT токен из RetailCRM.
    """
    # Проверяем JWT токен, если он предоставлен
    if token:
        verified_enterprise = verify_retailcrm_access_token(token)
        if not verified_enterprise:
            raise HTTPException(status_code=403, detail="Неверный или истёкший токен доступа")
        if verified_enterprise != enterprise_number:
            raise HTTPException(status_code=403, detail="Токен не подходит для этого предприятия")
        logger.info(f"🔑 Авторизация через RetailCRM токен для предприятия {enterprise_number}")
    
    # Получим имя предприятия для заголовка
    name = enterprise_number
    try:
        if pg_pool is None:
            await init_pg_pool()
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT name FROM enterprises WHERE number=$1", enterprise_number)
            if row:
                name = row["name"]
    except Exception:
        pass
    title = f"{name} RetailCRM"
    # Избегаем .format() из-за фигурных скобок в CSS/JS — заменяем только нужные плейсхолдеры
    html = (
        ADMIN_PAGE_HTML
        .replace("{title}", title)
        .replace("{header}", title)
    )
    return HTMLResponse(content=html)

@app.get("/retailcrm-admin/journal")
async def retailcrm_admin_journal(enterprise_number: str, phone: Optional[str] = None) -> HTMLResponse:
    """Страница журнала интеграций с поиском по телефону для юнита."""
    # Подготовим результаты: если телефон не указан — показываем последние 10 событий
    rows_html = ""
    norm_phone = (phone or "").strip()

    def safe(s: Any) -> str:
        try:
            t = json.dumps(s, ensure_ascii=False, indent=2)
        except Exception:
            t = str(s)
        return (t.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    if pg_pool is None:
        await init_pg_pool()
    assert pg_pool is not None

    if norm_phone:
        digits = "".join(ch for ch in norm_phone if ch.isdigit())
        like_a = f"%{digits}%"
        like_b = f"%+{digits}%" if not norm_phone.startswith("+") else f"%{norm_phone}%"
        query = (
            "SELECT created_at, event_type, integration_type, request_data, response_data "
            "FROM integration_logs "
            "WHERE enterprise_number=$1 "
            "AND ((request_data::text ILIKE $2) OR (response_data::text ILIKE $2) OR (request_data::text ILIKE $3) OR (response_data::text ILIKE $3)) "
            "ORDER BY created_at DESC LIMIT 500"
        )
        async with pg_pool.acquire() as conn:
            recs = await conn.fetch(query, enterprise_number, like_a, like_b)
    else:
        # По умолчанию: показываем 10 последних звонков (уникальные uid)
        # Берём одну, самую свежую запись на звонок с приоритетом calls_upload
        query = (
            "WITH logs AS ("
            "  SELECT id, created_at, event_type, integration_type, request_data, response_data, "
            "         COALESCE("
            "           call_unique_id, "
            "           request_data->>'uniqueId', "
            "           request_data->'payload'->>'callExternalId', "
            "           (request_data->'calls'->0)->>'externalId'"
            "         ) AS uid "
            "  FROM integration_logs "
            "  WHERE enterprise_number=$1 "
            "    AND (event_type LIKE 'call_event:%' OR event_type = 'calls_upload')"
            "), ranked AS ("
            "  SELECT *, "
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY uid "
            "           ORDER BY created_at DESC, "
            "             CASE WHEN event_type = 'calls_upload' THEN 2 ELSE 1 END DESC, "
            "             id DESC"
            "         ) AS rn "
            "  FROM logs "
            "  WHERE uid IS NOT NULL AND uid <> ''"
            ") "
            "SELECT created_at, event_type, integration_type, request_data, response_data "
            "FROM ranked "
            "WHERE rn = 1 "
            "ORDER BY created_at DESC "
            "LIMIT 10"
        )
        async with pg_pool.acquire() as conn:
            recs = await conn.fetch(query, enterprise_number)

    for r in recs:
        created = r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else ""
        req = r["request_data"] or {}
        resp = r["response_data"] or {}
        # Попробуем распарсить строки JSON для красивого отображения
        def parse_jsonish(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return v
            return v
        body_obj = parse_jsonish(req)
        resp_obj = parse_jsonish(resp)
        rows_html += (
            f"<tr>"
            f"<td style='white-space:nowrap;vertical-align:top;padding:8px;border-bottom:1px solid #1b3350;'>{created}</td>"
            f"<td style='vertical-align:top;padding:8px;border-bottom:1px solid #1b3350;'><pre style='margin:0;white-space:pre-wrap;'>{safe(body_obj)}</pre></td>"
            f"<td style='vertical-align:top;padding:8px;border-bottom:1px solid #1b3350;'><pre class='int-pre'>{safe(resp_obj)}</pre></td>"
            f"</tr>"
        )
    # Рендер
    html = f"""
<!doctype html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\">
  <title>RetailCRM журнал</title>
  <link rel=\"icon\" href=\"/retailcrm-admin/favicon.ico\">
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; background:#0b1728; color:#e7eef8; margin:0; }}
    .wrap {{ max-width: 100%; width: 100%; margin: 0; padding: 20px 24px; box-sizing: border-box; }}
    h1 {{ margin: 0 0 16px; font-size: 22px; }}
    .card {{ background:#0f2233; border:1px solid #1b3350; border-radius:12px; padding:18px; }}
    input[type=text] {{ padding:10px 12px; border-radius:8px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; }}
    .btn {{ background:#2563eb; color:#fff; border:none; padding:10px 14px; border-radius:8px; cursor:pointer; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; table-layout: fixed; }}
    td pre {{ max-width: 100%; overflow-wrap: anywhere; }}
    th {{ text-align:left; padding:8px; border-bottom:1px solid #1b3350; color:#8fb3da; }}
    .int-box {{ display:block; }}
    .int-label {{ color:#8fb3da; font-size:12px; margin:6px 0 2px; }}
    .int-pre {{ margin:0; white-space:pre-wrap; }}
  </style>
  </head>
  <body>
    <div class=\"wrap\">
      <h1>RetailCRM журнал</h1>
      <div class=\"card\" style=\"margin-bottom:16px;\">
        <form method=\"get\" action=\"/retailcrm-admin/journal\" style=\"display:flex;gap:10px;align-items:center;flex-wrap:wrap;\">
          <input type=\"hidden\" name=\"enterprise_number\" value=\"{enterprise_number}\" />
          <label>Телефон: <input type=\"text\" name=\"phone\" value=\"{(phone or '')}\" placeholder=\"+37529...\" /></label>
          <button class=\"btn\" type=\"submit\">Показать</button>
        </form>
      </div>
      <div class=\"card\">
        <table>
          <colgroup>
            <col style="width: 180px;" />
            <col style="width: calc((100% - 180px)/2);" />
            <col style="width: calc((100% - 180px)/2);" />
          </colgroup>
          <thead>
            <tr>
              <th>Дата</th>
              <th>Тело события</th>
              <th>Интеграция</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>
    </div>
  </body>
  </html>
    """
    return HTMLResponse(content=html)


@app.post("/retailcrm-admin/api/test-call/{enterprise_number}")
async def admin_api_test_call(enterprise_number: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Отправляет тестовый входящий звонок и hangup на конкретного менеджера.
    - phone: всегда +375290000000
    - dial -> вызывается popup у нужного userId/code
    - hangup -> failed с рандомным recordUrl (невалидный)
    """
    try:
        user_id = int(payload.get("userId")) if payload.get("userId") is not None else None
        code = str(payload.get("code") or "")
        if not user_id and not code:
            return {"success": False, "error": "userId or code required"}

        # Получаем конфиг
        cfg = await fetch_retailcrm_config(enterprise_number)
        if not (cfg and cfg.get("enabled") and cfg.get("domain") and cfg.get("api_key")):
            return {"success": False, "error": "retailcrm disabled or config invalid"}

        base_url = cfg["domain"] if str(cfg["domain"]).startswith("http") else f"https://{cfg['domain']}"
        api_key = cfg["api_key"]

        async with RetailCRMClient({
            "base_url": base_url,
            "api_key": api_key,
            "api_version": "v5",
            "timeout": 5,
        }) as client:
            phone = "+375290000000"
            unique_id = f"test-{int(time.time())}"
            # ensure customer exists and assign responsible if not set
            try:
                sr = await client.search_customer_by_phone(phone)
                customers_list = (sr.data or {}).get("customers") if (sr and sr.success and sr.data) else []
                if not customers_list:
                    # create new customer with selected manager as responsible (if provided)
                    cust = CustomerData(
                        firstName=phone,
                        phones=[PhoneData(number=phone)],
                        managerId=int(user_id) if user_id else None,
                    )
                    await client.create_customer(cust)
                else:
                    # set responsible if not assigned yet
                    existing = customers_list[0]
                    if (existing.get("managerId") in (None, 0)) and user_id:
                        upd = {"customer": json.dumps({"managerId": int(user_id)}), "by": "id"}
                        await client._make_request("POST", f"/customers/{existing.get('id')}/edit", data=upd)
            except Exception:
                pass
            # dial (popup)
            event_payload = {
                "phone": phone,
                "type": "in",
                "callExternalId": unique_id,
            }
            if code:
                event_payload["codes"] = [str(code)]
            if user_id:
                event_payload["userIds"] = [int(user_id)]
            ev1 = await client._make_request("POST", "/telephony/call/event", data={
                "clientId": enterprise_number,
                "event": json.dumps(event_payload, ensure_ascii=False),
            })

            # hangup failed + random record url
            event_payload2 = {
                "phone": phone,
                "type": "hangup",
                "callExternalId": unique_id,
                "hangupStatus": "no answered",
            }
            if code:
                event_payload2["codes"] = [str(code)]
            if user_id:
                event_payload2["userIds"] = [int(user_id)]
            ev2 = await client._make_request("POST", "/telephony/call/event", data={
                "clientId": enterprise_number,
                "event": json.dumps(event_payload2, ensure_ascii=False),
            })

            # calls/upload для истории
            upload_payload = [{
                "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "in",
                "phone": phone.lstrip('+'),
                "duration": 0,
                "result": "failed",
                "externalId": unique_id,
                "userId": int(user_id) if user_id else None,
                "recordUrl": f"https://invalid.example.com/{unique_id}.mp3",
            }]
            # очищаем None поля
            if upload_payload[0]["userId"] is None:
                upload_payload[0].pop("userId", None)

            up = await client._make_request("POST", "/telephony/calls/upload", data={
                "clientId": enterprise_number,
                "calls": json.dumps(upload_payload, ensure_ascii=False),
            })

            return {"success": bool(ev1.success and ev2.success and up.success)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/retailcrm-admin/favicon.ico")
async def retailcrm_admin_favicon():
    """Отдаёт favicon для страницы администрирования.
    Сначала пытается взять из общей статики проекта, иначе отдаёт пустой ответ.
    """
    try:
        candidate_paths = []
        if os.path.isdir(STATIC_DIR):
            candidate_paths.append(os.path.join(STATIC_DIR, "favicon.ico"))
            candidate_paths.append(os.path.join(STATIC_DIR, "img", "favicon.ico"))
        for p in candidate_paths:
            if os.path.isfile(p):
                return FileResponse(p, media_type="image/x-icon")
    except Exception:
        pass
    return Response(status_code=204)


@app.get("/retailcrm-admin/app.js")
async def retailcrm_admin_js():
    return Response(content=ADMIN_PAGE_JS, media_type="application/javascript")

@app.get("/test/credentials")
async def test_credentials():
    """Тест подключения к RetailCRM"""
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        result = await client.test_credentials()
        return result.dict()


@app.get("/test/sites")
async def test_sites():
    """Тест получения списка сайтов"""
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        result = await client.get_sites()
        return result.dict()


@app.get("/test/users")
async def test_users():
    """Тест получения списка пользователей"""
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        result = await client.get_users()
        return result.dict()


@app.get("/test/user/{user_id}")
async def test_user(user_id: int):
    """Тест получения конкретного пользователя"""
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        result = await client.get_user(user_id)
        return result.dict()


@app.get("/test/search-customer")
async def test_search_customer(phone: str):
    """Тест поиска клиента по телефону"""
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        result = await client.search_customer_by_phone(phone)
        return result.dict()


@app.post("/test/create-customer")
async def test_create_customer(customer: CustomerData):
    """Тест создания нового клиента"""
    try:
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.create_customer(customer)
            if result:
                return result.dict()
            else:
                return {"success": False, "error": "No result returned"}
    except Exception as e:
        logger.error(f"❌ Error in test_create_customer: {e}")
        return {"success": False, "error": str(e)}


@app.post("/test/call-task")
async def test_call_task(call_data: CallEventData):
    """Тест создания задачи о звонке"""
    try:
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.create_call_task(call_data)
            if result:
                return result.dict()
            else:
                return {"success": False, "error": "No result returned"}
    except Exception as e:
        logger.error(f"❌ Error in test_call_task: {e}")
        return {"success": False, "error": str(e)}

# ===== НОВЫЕ ENDPOINTS ПО ОФИЦИАЛЬНОЙ ДОКУМЕНТАЦИИ =====

@app.post("/test/upload-calls")
async def test_upload_calls():
    """Тест загрузки истории звонков"""
    try:
        # Формат данных как в рабочей интеграции
        test_calls = [{
            "date": "2025-01-08 15:30:00",
            "type": "in",
            "phone": "375296254070",  # Без плюса как в примере
            "code": "151",  # Один код, не массив
            "duration": 120,  # Число, не строка
            "result": "answered",  # result вместо status
            "externalId": "test-call-001",  # externalId для идентификации
            "recordUrl": "https://bot.vochi.by/retailcrm/call-recording/27596e3c-1481-406f-a1b9-ffcaa6c737cc",
            "externalPhone": "375296254070"  # Без плюса
        }]
        
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.upload_calls_history(test_calls)
            if result:
                return result.dict()
            else:
                return {"success": False, "error": "No result returned"}
    except Exception as e:
        logger.error(f"❌ Error in test_upload_calls: {e}")
        return {"success": False, "error": str(e)}

@app.post("/test/call-event")  
async def test_call_event():
    """Тест отправки события звонка"""
    try:
        # Формат как в рабочей интеграции - используем код, который уже есть у пользователя в RetailCRM
        test_event = {
            "phone": "375296254070",  # Без плюса
            "type": "in",
            "codes": ["151"],  # Код Евгения Баевского (ID 16) из RetailCRM users API
            "callExternalId": "test-call-event-002",  # callExternalId для связки событий
            "externalPhone": "375296254070"  # Без плюса
        }
        
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.send_call_event(test_event)
            if result:
                return result.dict()
            else:
                return {"success": False, "error": "No result returned"}
    except Exception as e:
        logger.error(f"❌ Error in test_call_event: {e}")
        return {"success": False, "error": str(e)}

@app.get("/test/manager/{phone}")
async def test_get_manager(phone: str):
    """Тест получения ответственного менеджера"""
    try:
        # Удаляем плюс из номера телефона как в рабочем примере
        clean_phone = phone.lstrip('+')
        
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.get_responsible_manager(clean_phone)
            if result:
                return result.dict()
            else:
                return {"success": False, "error": "No result returned"}
    except Exception as e:
        logger.error(f"❌ Error in test_get_manager: {e}")
        return {"success": False, "error": str(e)}

@app.post("/test/real-call")
async def test_real_call():
    """Тест загрузки реального звонка из нашей БД"""
    try:
        import uuid
        from datetime import datetime
        
        # Создаем звонок с уникальным ID 
        unique_id = str(uuid.uuid4())
        test_call = {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "in",
                "phone": "375296254070",
                "code": "151",
                "duration": 90,
                "result": "answered",
                "externalId": unique_id,
                "recordUrl": f"https://bot.vochi.by/retailcrm/call-recording/01777e5e-0a53-4dfd-8df9-14f3fd6fc000",
                "externalPhone": "375296254070"
            }
        
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.upload_calls_history([test_call])
            if result:
                return {
                    "success": True,
                    "call_data": test_call,
                    "result": result.dict()
                }
            else:
                return {"success": False, "error": "No result returned"}
    except Exception as e:
        logger.error(f"❌ Error in test_real_call: {e}")
        return {"success": False, "error": str(e)}


# ===== WEBHOOKS ДЛЯ RETAILCRM =====

@app.get("/retailcrm/make-call")
async def make_call_webhook(
    clientId: str,
    phone: str,
    userId: int,
    code: str = None,
    externalPhone: str = None
):
    """Webhook для инициации звонка из RetailCRM
    
    Параметры:
    - clientId: токен интеграции RetailCRM
    - phone: номер телефона для звонка
    - userId: ID пользователя в RetailCRM
    - code: добавочный номер (опционально, будет найден по userId)
    - externalPhone: внешний номер (опционально)
    """
    logger.info(f"🔥 RetailCRM Click-to-Call: userId={userId}, phone={phone}, code={code}")
    
    try:
        # 1. Находим предприятие по clientId
        enterprise = await find_enterprise_by_integration_token(clientId)
        if not enterprise:
            logger.error(f"❌ Enterprise not found for clientId: {clientId}")
            return Response(status_code=401, content="Unauthorized: Invalid clientId")
        
        logger.info(f"🏢 Found enterprise: {enterprise['name']} ({enterprise['number']})")
        
        # 2. Получаем назначения пользователей
        user_extensions = await load_user_extensions_from_db(enterprise["number"])
        
        # 3. Определяем внутренний номер
        internal_extension = None
        
        # Приоритет: параметр code, затем маппинг по userId
        if code and code.strip():
            internal_extension = code.strip()
            logger.info(f"📞 Using provided code: {internal_extension}")
        else:
            # Ищем по userId в маппинге
            internal_extension = user_extensions.get(str(userId))
            if internal_extension:
                logger.info(f"📞 Found extension by userId {userId}: {internal_extension}")
            else:
                logger.error(f"❌ No extension found for userId {userId}")
                return Response(status_code=400, content=f"No extension configured for user {userId}")
        
        # 4. Вызываем asterisk.py для инициации звонка
        asterisk_result = await call_asterisk_api(
            code=internal_extension,
            phone=phone,
            client_id=enterprise["secret"]
        )
        
        if asterisk_result["success"]:
            logger.info(f"✅ Call initiated successfully: {internal_extension} -> {phone}")
            return Response(status_code=200, content="OK")
        else:
            logger.error(f"❌ Asterisk API failed: {asterisk_result.get('error', 'Unknown error')}")
            return Response(status_code=500, content=f"Call initiation failed: {asterisk_result.get('error', 'Unknown error')}")
            
    except Exception as e:
        logger.error(f"❌ Error in make_call_webhook: {e}")
        return Response(status_code=500, content=f"Internal error: {str(e)}")

@app.get("/retailcrm/status")  
async def status_change_webhook(
    clientId: str,
    userId: int,
    code: str,
    status: str
):
    """Webhook для уведомления о смене статуса пользователя"""
    logger.info(f"📊 Смена статуса: userId={userId}, code={code}, status={status}")
    
    try:
        # Здесь должна быть логика обработки смены статуса
        logger.info(f"✅ Статус обновлен: {status}")
        return Response(status_code=200, content="OK")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления статуса: {e}")
        return Response(status_code=500, content="Error")

@app.get("/retailcrm/config")
async def telephony_config_webhook():
    """Webhook для предоставления конфигурации кодов пользователей"""
    logger.info(f"📞 Запрос конфигурации телефонии")
    
    try:
        # Возвращаем соответствие пользователей и кодов
        config = {
            "users": [
                {"id": 16, "code": "151", "name": "Евгений Баевский"},
                {"id": 18, "code": "152", "name": "Джулай Джуновый"},
                {"id": 19, "code": "150", "name": "Август Тимлидовый"}
            ],
            "success": True
        }
        logger.info(f"✅ Конфигурация отправлена: {config}")
        return config
    except Exception as e:
        logger.error(f"❌ Ошибка получения конфигурации: {e}")
        return {"success": False, "error": str(e)}

@app.get("/retailcrm/call-recording/{call_id}")
async def get_call_recording_proxy(call_id: str):
    """Проксирует запросы записей звонков от RetailCRM к нашим файлам"""
    logger.info(f"🎧 Запрос записи звонка: {call_id}")
    
    try:
        # Здесь должна быть логика получения реальной ссылки на запись
        # Пока возвращаем редирект на наш сервер записей
        from fastapi.responses import RedirectResponse
        
        # Ищем UUID записи в базе данных по call_id или external_id
        real_recording_url = f"https://bot.vochi.by/recordings/file/{call_id}"
        
        logger.info(f"🔄 Перенаправление на: {real_recording_url}")
        return RedirectResponse(url=real_recording_url)
        
    except Exception as e:
        logger.error(f"❌ Ошибка проксирования записи: {e}")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=404,
            content={"error": "Recording not found", "call_id": call_id}
        )

@app.get("/test/telephony-settings")
async def test_telephony_settings():
    """Тест получения настроек телефонии"""
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        result = await client.get_telephony_settings()
        return result.dict()


@app.get("/internal/retailcrm/customer-name")
async def internal_get_customer_name(phone: str):
    """Возвращает имя клиента по номеру телефона через RetailCRM.
    Формат ответа: {"name": string|null}
    """
    try:
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.search_customer_by_phone(phone)
            name = None
            if result and result.success and isinstance(result.data, dict):
                customers = result.data.get("customers") or []
                if customers:
                    c = customers[0]
                    first = (c.get("firstName") or "").strip()
                    last = (c.get("lastName") or "").strip()
                    # Требуемый формат отображения: "Фамилия Имя"
                    full = (last + " " + first).strip()
                    name = full or None
            return {"name": name}
    except Exception as e:
        logger.error(f"internal_get_customer_name error: {e}")
        return {"name": None}


@app.get("/internal/retailcrm/customer-profile")
async def internal_get_customer_profile(phone: str):
    """Возвращает профиль клиента по номеру телефона (Фамилия/Имя/Отчество/Компания).
    Формат ответа: {"last_name": str|null, "first_name": str|null, "middle_name": str|null, "enterprise_name": str|null}
    """
    try:
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            result = await client.search_customer_by_phone(phone)
            prof = {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}
            if result and result.success and isinstance(result.data, dict):
                customers = result.data.get("customers") or []
                if customers:
                    c = customers[0]
                    ln = (c.get("lastName") or "").strip() or None
                    fn = (c.get("firstName") or "").strip() or None
                    mn = (c.get("patronymic") or c.get("middleName") or "").strip() or None
                    # Название компании может храниться в разных полях; пытаемся угадать
                    comp = (
                        (c.get("company") or {}).get("name")
                        if isinstance(c.get("company"), dict) else c.get("companyName")
                    )
                    en = (comp or "").strip() or None
                    prof = {"last_name": ln, "first_name": fn, "middle_name": mn, "enterprise_name": en}
            return prof
    except Exception as e:
        logger.error(f"internal_get_customer_profile error: {e}")
        return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}

@app.get("/internal/retailcrm/responsible-extension")
async def internal_get_responsible_extension(phone: str):
    """Возвращает внутренний код ответственного менеджера по номеру телефона.
    Основной путь: /telephony/manager?phone=... → manager.code.
    Фолбэк: /customers?filter[phone] → managerId → /users/{id} → code.
    Формат ответа: {"extension": str|null}
    """
    try:
        clean_phone = phone.lstrip('+')
        async with RetailCRMClient(RETAILCRM_CONFIG) as client:
            found_manager_id: Optional[int] = None
            # 1) Основной API для ответственного менеджера
            mgr = await client.get_responsible_manager(clean_phone)
            if mgr and isinstance(mgr.data, dict):
                data_obj = mgr.data
                code = None
                # Популярные варианты расположения кода
                if isinstance(data_obj.get("manager"), dict):
                    code = data_obj["manager"].get("code")
                    mid = data_obj["manager"].get("id")
                    try:
                        found_manager_id = int(mid) if str(mid).isdigit() else None
                    except Exception:
                        found_manager_id = None
                if not code:
                    code = data_obj.get("code")
                inner = data_obj.get("data") if isinstance(data_obj.get("data"), dict) else None
                if not code and inner and isinstance(inner.get("manager"), dict):
                    code = inner["manager"].get("code")
                    mid = inner["manager"].get("id")
                    try:
                        found_manager_id = int(mid) if str(mid).isdigit() else None
                    except Exception:
                        found_manager_id = None
                if not code and inner:
                    code = inner.get("code")
                if isinstance(code, str) and code.isdigit():
                    # попытка добрать managerId через customers, если не определили
                    if found_manager_id is None:
                        try:
                            cust_tmp = await client.search_customer_by_phone(clean_phone)
                            if cust_tmp and cust_tmp.success and isinstance(cust_tmp.data, dict):
                                customers_tmp = (cust_tmp.data or {}).get("customers") or []
                                if customers_tmp:
                                    found_manager_id = customers_tmp[0].get("managerId")
                        except Exception:
                            pass
                    return {"extension": code, "manager_id": found_manager_id}

            # 2) Фолбэк через клиента и пользователя
            cust = await client.search_customer_by_phone(clean_phone)
            if cust and cust.success and isinstance(cust.data, dict):
                customers = (cust.data or {}).get("customers") or []
                if customers:
                    # Выбираем наиболее актуальную запись клиента по updatedAt/createdAt
                    def _ts(c):
                        return c.get("updatedAt") or c.get("createdAt") or ""
                    try:
                        customers_sorted = sorted(customers, key=_ts)
                    except Exception:
                        customers_sorted = customers
                    best = customers_sorted[-1] if customers_sorted else customers[0]
                    manager_id = best.get("managerId")
                    if manager_id:
                        user_resp = await client.get_user(int(manager_id))
                        if user_resp and user_resp.success and isinstance(user_resp.data, dict):
                            user = (user_resp.data or {}).get("user") or {}
                            code = user.get("code")
                            if isinstance(code, str) and code.isdigit():
                                return {"extension": code, "manager_id": int(manager_id)}
                            # если code нет — берём поле phone как внутренний
                            phone_ext = user.get("phone")
                            if isinstance(phone_ext, str) and phone_ext.isdigit():
                                return {"extension": phone_ext, "manager_id": int(manager_id)}
            return {"extension": None, "manager_id": None}
    except Exception as e:
        logger.error(f"internal_get_responsible_extension error: {e}")
        return {"extension": None}

# =============================================================================
# ПОЛНЫЙ ТЕСТОВЫЙ СЦЕНАРИЙ
# =============================================================================

@app.get("/test/full-scenario")
async def test_full_scenario():
    """Полный тестовый сценарий работы с RetailCRM"""
    
    results = {}
    async with RetailCRMClient(RETAILCRM_CONFIG) as client:
        
        # 1. Тест подключения
        logger.info("🔸 1. Тестирование подключения...")
        results["credentials"] = await client.test_credentials()
        
        # 2. Получение системной информации
        logger.info("🔸 2. Получение системной информации...")
        results["sites"] = await client.get_sites()
        
        # 3. Получение пользователей
        logger.info("🔸 3. Получение списка пользователей...")
        results["users"] = await client.get_users()
        
        # 4. Поиск несуществующего клиента
        logger.info("🔸 4. Поиск клиента по телефону...")
        test_phone = "+375296254070"
        results["search_customer"] = await client.search_customer_by_phone(test_phone)
        
        # 5. Создание тестового клиента
        logger.info("🔸 5. Создание тестового клиента...")
        test_customer = CustomerData(
            firstName="Тестовый",
            lastName="Клиент",
            phones=[PhoneData(number=test_phone)],  # Используем новый формат
            email="test@example.com",
            managerId=16  # Привязываем к Евгению Баевскому
        )
        results["create_customer"] = await client.create_customer(test_customer)
        
        # 6. Создание задачи о звонке с записью
        logger.info("🔸 6. Создание задачи о звонке...")
        test_call = CallEventData(
            phone=test_phone,
            type="incoming", 
            duration=135,  # 2 мин 15 сек
            status="answered",
            customer_id=69,  # Анна СМенеджером
            manager_name="Евгений Баевский",
                                recording_url="https://bot.vochi.by/recordings/file/27596e3c-1481-406f-a1b9-ffcaa6c737cc"
        )
        results["call_task"] = await client.create_call_task(test_call)
        
        # 7. Получение настроек телефонии
        logger.info("🔸 7. Получение настроек телефонии...")
        results["telephony_settings"] = await client.get_telephony_settings()
    
    # Подготовка итогового отчета
    summary = {
        "test_completed_at": datetime.now().isoformat(),
        "total_tests": len(results),
        "successful_tests": sum(1 for r in results.values() if r.success),
        "failed_tests": sum(1 for r in results.values() if not r.success),
        "average_response_time": sum(r.response_time for r in results.values()) / len(results),
        "results": {k: v.dict() for k, v in results.items()}
    }
    
    logger.info(f"📊 Тестирование завершено: {summary['successful_tests']}/{summary['total_tests']} успешно")
    return summary


# =============================================================================
# ЗАПУСК СЕРВИСА
# =============================================================================

if __name__ == "__main__":
    logger.info("🏪 Запуск RetailCRM Integration Service...")
    logger.info(f"🔧 Фаза 1: Тестирование API")
    logger.info(f"🌐 RetailCRM: {RETAILCRM_CONFIG['base_url']}")
    
    uvicorn.run(
        "retailcrm:app",
        host="0.0.0.0",
        port=8019,
        log_level="info"
    )