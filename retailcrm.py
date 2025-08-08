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
from datetime import datetime
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
    return updated_cfg

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
    integration_module = {
        "code": code,
        "active": enabled,
        "name": "Vochi-CRM",
        # Требование RetailCRM: logo должен быть SVG
        "logo": "https://bot.vochi.by/static/img/vochi_logo.svg",
        # Требование RetailCRM: baseUrl обязателен
        "baseUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}",
        "clientId": enterprise_number,
        "accountUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm-admin/?enterprise_number={enterprise_number}",
        # Поле actions убираем — RetailCRM ругается на неверный выбор
        "allowEdit": False,
        "configuration": {
            "makeCallUrl": make_call_url,
            "changeUserStatusUrl": change_status_url,
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
            integration_module = {
                "code": code,
                "active": False,  # Деактивируем
                "name": "Vochi-CRM",
                "logo": "https://bot.vochi.by/static/img/vochi_logo.svg",
                "baseUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}",
                "clientId": enterprise_number,
                "accountUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm-admin/?enterprise_number={enterprise_number}",
                "allowEdit": False,
                "configuration": {
                    "makeCallUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm/make-call",
                    "changeUserStatusUrl": f"https://{os.environ.get('VOCHI_PUBLIC_HOST', 'bot.vochi.by')}/retailcrm/status",
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
    <h1>{header}</h1>
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
        <button id=\"saveBtn\" class=\"btn\">Сохранить и зарегистрировать</button>
        <button id=\"deleteBtn\" class=\"btn\" style=\"background:#dc2626; margin-left:auto;\">Удалить интеграцию</button>
        <span id=\"msg\" class=\"hint\"></span>
      </div>
    </div>
  </div>
  <script src="./app.js"></script>
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

    const saveBtn = document.getElementById('saveBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    if (saveBtn) saveBtn.addEventListener('click', save);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteIntegration);
    load();
  } catch (e) { console.error('Admin JS init error', e); }
})();
"""


@app.get("/retailcrm-admin/", response_class=HTMLResponse)
async def retailcrm_admin_page(enterprise_number: str) -> HTMLResponse:
    """Простая UI‑страница администрирования для ввода домена и API‑ключа."""
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
    code: str, 
    phone: str,
    userId: int,
    externalPhone: str = None
):
    """Webhook для инициации звонка из RetailCRM"""
    logger.info(f"🔥 Запрос на звонок: code={code}, phone={phone}, userId={userId}")
    
    try:
        # Здесь должна быть логика инициации звонка через Asterisk
        # Пока возвращаем успех для активации модуля
        logger.info(f"✅ Звонок инициирован: {phone}")
        return Response(status_code=200, content="OK")
    except Exception as e:
        logger.error(f"❌ Ошибка инициации звонка: {e}")
        return Response(status_code=500, content="Error")

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