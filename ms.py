#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🏪 МойСклад Integration Service
================================

Сервис для интеграции системы телефонии с МойСклад.
Обеспечивает взаимодействие через API Remap 1.2.

Автор: AI Assistant
Дата: 31.01.2025
Версия: 1.0 (Фаза 1 - Базовая интеграция)
"""

import asyncio
import os
import json
import logging
import sys
import time
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

import aiohttp
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncpg

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

# Глобальный set для отслеживания обработанных hangup событий
processed_hangup_events = set()

# Кэш конфигураций МойСклад для уменьшения запросов к БД/cache
ms_config_cache = {}  # {enterprise_number: {"config": {...}, "expires": timestamp}}
MS_CONFIG_CACHE_TTL = 300  # 5 минут

# МойСклад настройки по умолчанию
MOYSKLAD_CONFIG = {
    "base_url": "https://api.moysklad.ru/api/remap/1.2",
    "login": "",
    "password": "",
    "api_version": "1.2",
    "timeout": 30
}

# JWT конфигурация для токенов доступа МойСклад
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "vochi-moysklad-secret-key-2025")
JWT_ALGORITHM = "HS256"

# Настройки логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/root/asterisk-webhook/logs/ms.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("MoySklad")

# =============================================================================
# МОДЕЛИ ДАННЫХ
# =============================================================================

class MoySkladResponse(BaseModel):
    """Стандартный ответ от МойСклад API"""
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
    name: Optional[str] = None
    phone: Optional[str] = None  # Старое поле для совместимости
    phones: Optional[List[PhoneData]] = None  # Новое поле для массива телефонов
    email: Optional[str] = None
    tags: Optional[List[str]] = None
    manager_id: Optional[str] = None  # ID менеджера для привязки


class OrderData(BaseModel):
    """Данные заказа"""
    customer_id: str
    description: Optional[str] = None
    source: Optional[str] = None
    status_id: Optional[str] = None


class CallEventData(BaseModel):
    """Данные события звонка для создания заметки"""
    phone: str
    type: str  # incoming, outgoing
    duration: Optional[int] = None
    status: str  # answered, busy, failed, etc.
    customer_id: Optional[str] = None  # ID клиента в МойСклад
    manager_name: Optional[str] = None  # Имя менеджера
    recording_url: Optional[str] = None  # Ссылка на запись


# =============================================================================
# ОСНОВНОЙ СЕРВИС
# =============================================================================

app = FastAPI(
    title="МойСклад Integration Service",
    version="1.0.0",
    description="Сервис интеграции с МойСклад через API Remap 1.2"
)

# Глобальные переменные для кэширования
_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_LAST_CONFIG_UPDATE: Dict[str, float] = {}

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

async def get_enterprise_config(enterprise_number: str) -> Dict[str, Any]:
    """Получение конфигурации предприятия из БД"""
    cache_key = f"config_{enterprise_number}"
    current_time = time.time()

    # Проверяем кэш (5 минут)
    if (cache_key in _CONFIG_CACHE and
        current_time - _LAST_CONFIG_UPDATE.get(cache_key, 0) < 300):
        return _CONFIG_CACHE[cache_key]

    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )

        # Получаем конфигурацию из таблицы moy_sklad_config
        row = await conn.fetchrow(
            "SELECT config FROM moy_sklad_config WHERE enterprise_number = $1",
            enterprise_number
        )

        await conn.close()

        if row:
            config = row["config"]
            # Обновляем кэш
            _CONFIG_CACHE[cache_key] = config
            _LAST_CONFIG_UPDATE[cache_key] = current_time
            return config
        else:
            # Возвращаем конфигурацию по умолчанию
            default_config = {
                "enabled": False,
                "login": "",
                "password": "",
                "api_url": MOYSKLAD_CONFIG["base_url"],
                "notifications": {
                    "call_notify_mode": "none",
                    "notify_incoming": False,
                    "notify_outgoing": False
                },
                "incoming_call_actions": {
                    "create_order": False,
                    "order_status": "Новый",
                    "order_source": "Телефонный звонок"
                },
                "outgoing_call_actions": {
                    "create_order": False,
                    "order_status": "Новый",
                    "order_source": "Исходящий звонок"
                }
            }
            return default_config

    except Exception as e:
        logger.error(f"Failed to get enterprise config: {e}")
        return {
            "enabled": False,
            "login": "",
            "password": "",
            "api_url": MOYSKLAD_CONFIG["base_url"],
            "notifications": {"call_notify_mode": "none", "notify_incoming": False, "notify_outgoing": False},
            "incoming_call_actions": {"create_order": False, "order_status": "Новый", "order_source": "Телефонный звонок"},
            "outgoing_call_actions": {"create_order": False, "order_status": "Новый", "order_source": "Исходящий звонок"}
        }


async def moy_sklad_client(login: str, password: str) -> aiohttp.ClientSession:
    """Создание HTTP клиента для МойСклад API с аутентификацией"""
    auth = aiohttp.BasicAuth(login, password)
    timeout = aiohttp.ClientTimeout(total=30.0, connect=10.0)
    return aiohttp.ClientSession(
        auth=auth,
        timeout=timeout,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    )


async def moy_sklad_request(
    method: str,
    url: str,
    login: str,
    password: str,
    data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Выполнение запроса к МойСклад API"""
    start_time = time.time()

    try:
        async with await moy_sklad_client(login, password) as session:
            json_data = json.dumps(data) if data else None

            async with session.request(
                method=method,
                url=url,
                data=json_data
            ) as response:

                response_time = time.time() - start_time

                try:
                    result_data = await response.json()
                except:
                    result_data = {"status_code": response.status}

                if response.status == 200:
                    return MoySkladResponse(
                        success=True,
                        data=result_data,
                        response_time=response_time,
                        endpoint=url
                    ).dict()
                else:
                    error_msg = result_data.get("errors", [{}])[0].get("error", f"HTTP {response.status}")
                    return MoySkladResponse(
                        success=False,
                        error=error_msg,
                        data=result_data,
                        response_time=response_time,
                        endpoint=url
                    ).dict()

    except Exception as e:
        response_time = time.time() - start_time
        return MoySkladResponse(
            success=False,
            error=str(e),
            response_time=response_time,
            endpoint=url
        ).dict()


async def log_integration_event(
    enterprise_number: str,
    event_type: str,
    request_data: Optional[Dict[str, Any]] = None,
    response_data: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    status: str = "success"
) -> None:
    """Логирование события интеграции в БД"""
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )

        await conn.execute("""
            INSERT INTO integration_logs
            (enterprise_number, integration_type, event_type, request_data, response_data, error_message, status, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
        """,
        enterprise_number,
        "ms",
        event_type,
        json.dumps(request_data) if request_data else None,
        json.dumps(response_data) if response_data else None,
        error_message,
        status
        )

        await conn.close()

    except Exception as e:
        logger.error(f"Failed to log integration event: {e}")


# =============================================================================
# ОСНОВНЫЕ API ФУНКЦИИ
# =============================================================================

async def get_customer_by_phone(
    phone: str,
    login: str,
    password: str,
    api_url: str
) -> Optional[Dict[str, Any]]:
    """Поиск клиента по номеру телефона"""
    try:
        # Убираем все нецифровые символы из телефона
        clean_phone = ''.join(filter(str.isdigit, phone))

        # Ищем контрагентов по телефону
        url = f"{api_url}/entity/counterparty"
        params = {
            "filter": f"phone={clean_phone}",
            "limit": 1
        }

        response = await moy_sklad_request("GET", url, login, password)

        if response["success"] and response["data"]["rows"]:
            customer = response["data"]["rows"][0]
            return {
                "id": customer["id"],
                "name": customer["name"],
                "phone": clean_phone,
                "email": customer.get("email", ""),
                "tags": customer.get("tags", []),
                "manager": customer.get("owner", {}).get("name", "") if customer.get("owner") else "",
                "manager_id": customer.get("owner", {}).get("id", "") if customer.get("owner") else ""
            }

        return None

    except Exception as e:
        logger.error(f"Error getting customer by phone: {e}")
        return None


async def create_customer(
    customer_data: Dict[str, Any],
    api_token: str,
    api_url: str = "https://api.moysklad.ru/api/remap/1.2"
) -> Optional[str]:
    """Создание нового клиента через Bearer token"""
    try:
        url = f"{api_url}/entity/counterparty"

        # Формируем данные для создания контрагента
        data = {
            "name": customer_data["name"],
            "phone": customer_data["phone"],
            "email": customer_data.get("email", ""),
            "tags": customer_data.get("tags", [])
        }
        
        # Добавляем владельца, если указан
        if customer_data.get("owner_id"):
            data["owner"] = {"meta": {"href": f"https://api.moysklad.ru/api/remap/1.2/entity/employee/{customer_data['owner_id']}", "type": "employee"}}

        logger.info(f"🆕 Creating customer via Main API: {data['name']} ({data['phone']})")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Accept": "application/json;charset=utf-8",
                    "Content-Type": "application/json;charset=utf-8"
                },
                json=data
            )
            
            logger.info(f"📞 Create customer response: status={response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                customer_id = result.get("id")
                logger.info(f"✅ Customer created successfully: {customer_id}")
                return customer_id
            else:
                logger.error(f"❌ Failed to create customer: {response.status_code} - {response.text}")
                return None

    except Exception as e:
        logger.error(f"❌ Error creating customer: {e}")
        return None


async def create_order(
    order_data: Dict[str, Any],
    login: str,
    password: str,
    api_url: str
) -> Optional[str]:
    """Создание заказа"""
    try:
        url = f"{api_url}/entity/customerorder"

        data = {
            "agent": {
                "meta": {
                    "href": f"{api_url}/entity/counterparty/{order_data['customer_id']}",
                    "type": "counterparty"
                }
            },
            "description": order_data.get("description", "Создано из телефонного звонка"),
            "source": {
                "meta": {
                    "href": f"{api_url}/entity/saleschannel/{order_data.get('source_id', '')}",
                    "type": "saleschannel"
                }
            } if order_data.get("source_id") else None
        }

        # Убираем None значения
        data = {k: v for k, v in data.items() if v is not None}

        response = await moy_sklad_request("POST", url, login, password, data)

        if response["success"]:
            return response["data"]["id"]

        return None

    except Exception as e:
        logger.error(f"Error creating order: {e}")
        return None


async def get_organization(login: str, password: str, api_url: str) -> Optional[Dict[str, Any]]:
    """Получение информации об организации"""
    try:
        url = f"{api_url}/entity/organization"
        params = {"limit": 1}

        response = await moy_sklad_request("GET", url, login, password)

        if response["success"] and response["data"]["rows"]:
            org = response["data"]["rows"][0]
            return {
                "id": org["id"],
                "name": org["name"],
                "inn": org.get("inn", ""),
                "kpp": org.get("kpp", "")
            }

        return None

    except Exception as e:
        logger.error(f"Error getting organization: {e}")
        return None


# =============================================================================
# FASTAPI ЭНДПОИНТЫ
# =============================================================================

# =============================================================================
# АДМИНКА HTML ШАБЛОН
# =============================================================================

MS_ADMIN_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{enterprise_name} МойСклад</title>
  <link rel="icon" href="/ms-admin/favicon.ico"> 
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background:#0b1728; color:#e7eef8; }
    .wrap { max-width: 820px; margin: 0 auto; padding: 28px; }
    h1 { font-size: 24px; margin: 0 0 18px; }
    .card { background:#0f2233; border:1px solid #1b3350; border-radius:12px; padding:22px; }
    label { display:block; margin:12px 0 8px; color:#a8c0e0; font-size:14px; }
    input[type=text], input[type=password], input[type=url] { width:100%; padding:12px 14px; border-radius:10px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:16px; }
    .row { display:flex; gap:16px; flex-wrap: wrap; }
    .row > div { flex:1 1 320px; }
    .actions { margin-top:20px; display:flex; align-items:center; gap:16px; }
    .btn { background:#2563eb; color:#fff; border:none; padding:12px 18px; border-radius:10px; cursor:pointer; font-size:16px; }
    .btn:disabled { opacity:.6; cursor:not-allowed; }
    input[type=checkbox] { width:20px; height:20px; accent-color:#2563eb; }
    .hint { color:#8fb3da; font-size:13px; margin-top:6px; }
    .success { color:#4ade80; }
    .error { color:#f87171; }
    .webhook-url { background:#1a2b42; border:1px solid #2c4a6e; border-radius:8px; padding:12px; margin:8px 0; font-family:monospace; font-size:14px; word-break:break-all; }
    .copy-btn { background:#059669; color:#fff; border:none; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:12px; margin-left:8px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div style="display:flex; align-items:center; margin-bottom:20px;">
      <h1 style="margin:0; margin-right:15px;">{enterprise_name} МойСклад</h1>
      <img src="/ms.png" alt="МойСклад" style="height:48px; width:auto; background:white; padding:4px; border-radius:4px; border:1px solid #ddd;">
    </div>
    <div class="card">
      <div class="row">
        <div>
          <label>Phone API URL</label>
          <input id="phoneApiUrl" type="url" value="https://api.moysklad.ru/api/phone/1.0" readonly style="background:#1a2b42; opacity:0.7;" />
          <div class="hint">Адрес Phone API МойСклад (зафиксирован)</div>
        </div>
        <div>
          <label>Ключ интеграции</label>
          <input id="integrationCode" type="text" value="" placeholder="165e3202-66ea-46e5-ab4f-3c65ad41d9ab" />
          <div class="hint">Ключ от МойСклад для интеграции телефонии</div>
        </div>
        <div>
          <label>Токен основного API</label>
          <input id="apiToken" type="text" value="" placeholder="cd5134fa1beec235ed6cc3c4973d4daf540bab8b" />
          <div class="hint">Токен для основного API МойСклад 1.2 (получение клиентов)</div>
        </div>
      </div>

      <div style="margin:16px 0;">
        <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Webhook URL для настройки в МойСклад:</label>
        <div class="webhook-url" id="webhookUrl">
          https://bot.vochi.by/ms/webhook/loading...
        </div>
        <button type="button" class="copy-btn" onclick="copyWebhookUrl()">📋 Копировать</button>
        <div class="hint">Скопируйте и вставьте этот URL в настройки интеграции МойСклад</div>
      </div>
      <div class="actions">
        <label><input id="enabled" type="checkbox" /> Активен?</label>
        <button id="saveBtn" type="button" class="btn">Сохранить</button>
        <button id="deleteBtn" type="button" class="btn" style="background:#dc2626; margin-left:auto;">Удалить интеграцию</button>
        <button id="journalBtn" type="button" class="btn" style="background:#374151;">Журнал</button>
        <span id="msg" class="hint"></span>
      </div>

      <!-- Секция сотрудников -->
      <div style="margin-top: 32px; border-top: 1px solid #2d3a52; padding-top: 24px;">
        <h3 style="color: #ffffff; margin-bottom: 16px; font-size: 18px;">
          👥 Сотрудники МойСклад
        </h3>
        
        <div style="margin-bottom: 16px;">
          <button id="loadEmployeesBtn" type="button" class="btn" style="background:#059669; margin-right: 12px;">
            🔄 Загрузить сотрудников
          </button>
          <span id="employeesStatus" style="color: #a8c0e0; font-size: 14px;"></span>
        </div>

        <div id="employeesContainer" style="display: none;">
          <div style="background: #1e2537; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
            <div style="display: grid; grid-template-columns: 3fr 1fr 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid #2d3a52; margin-bottom: 12px; font-weight: bold; color: #a8c0e0;">
              <div>ФИО</div>
              <div>Внутренний номер</div>
              <div>Тестирование</div>
            </div>
            <div id="employeesList"></div>
          </div>
          
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 14px; color: #a8c0e0;">
            <div id="employeesTotal"></div>
            <div id="employeesApiStatus"></div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Блок дополнительных настроек -->
    <div class="card" style="margin-top:20px;">
      <h2 style="margin:0 0 15px 0; font-size:20px; color:#e7eef8;">Дополнительные настройки</h2>
      
      <div style="margin-top:20px;">
        <h3 style="margin:0 0 15px 0; font-size:18px; color:#e7eef8;">Уведомления</h3>
        
        <div style="margin-bottom:15px;">
          <div style="color:#a8c0e0; font-size:14px; margin-bottom:8px;">Уведомления о звонке</div>
          <div style="display:flex; gap:20px; align-items:center; margin-bottom:10px;">
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="callNotifyMode" value="none" style="width:16px; height:16px; accent-color:#2563eb;">
              Не уведомлять
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="callNotifyMode" value="during" checked style="width:16px; height:16px; accent-color:#2563eb;">
              Во время дозвона
            </label>
          </div>
        </div>
        
        <div style="display:flex; gap:20px; align-items:center;">
          <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="notifyIncoming" style="width:16px; height:16px; accent-color:#2563eb;" checked>
            Уведомлять при входящем
          </label>
          <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="notifyOutgoing" style="width:16px; height:16px; accent-color:#2563eb;">
            Уведомлять при исходящем
          </label>
        </div>
      </div>
      
      <div style="margin-top:30px;">
        <h3 style="margin:0 0 15px 0; font-size:18px; color:#e7eef8;">Действие при входящем звонке</h3>
        
        <div style="margin-bottom:15px;">
          <label style="display:flex; align-items:center; gap:8px; margin:0 0 10px 0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="createClientOnCall" style="width:16px; height:16px; accent-color:#2563eb;" checked>
            Создание клиента при неизвестном звонке
          </label>
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="color:#a8c0e0; font-size:14px; margin-bottom:8px;">Создание заказа</div>
          <div style="display:flex; gap:15px; align-items:center; margin-bottom:10px;">
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createOrder" value="none" checked style="width:16px; height:16px; accent-color:#2563eb;">
              Не создавать
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createOrder" value="always" style="width:16px; height:16px; accent-color:#2563eb;">
              Всегда создавать
            </label>
          </div>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Источник заказа</label>
          <input type="text" id="orderSource" value="Телефонный звонок" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
        </div>
      </div>
      
      <div style="margin-top:30px;">
        <h3 style="margin:0 0 15px 0; font-size:18px; color:#e7eef8;">Действия при исходящем звонке</h3>
        
        <div style="margin-bottom:15px;">
          <label style="display:flex; align-items:center; gap:8px; margin:0 0 10px 0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="createClientOnOutgoing" style="width:16px; height:16px; accent-color:#2563eb;">
            Создание клиента при неизвестном номере
          </label>
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="color:#a8c0e0; font-size:14px; margin-bottom:8px;">Создание заказа</div>
          <div style="display:flex; gap:15px; align-items:center; margin-bottom:10px;">
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createOrderOutgoing" value="none" checked style="width:16px; height:16px; accent-color:#2563eb;">
              Не создавать
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createOrderOutgoing" value="always" style="width:16px; height:16px; accent-color:#2563eb;">
              Всегда создавать
            </label>
          </div>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Источник заказа</label>
          <input type="text" id="orderSourceOutgoing" value="Исходящий звонок" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
        </div>
      </div>
    </div>
  </div>
  <script>
  (function(){
  try {
    const qs = new URLSearchParams(location.search);
    const enterprise = qs.get('enterprise_number');

    // Функция копирования webhook URL
    window.copyWebhookUrl = function() {
      const webhookUrl = document.getElementById('webhookUrl').textContent;
      navigator.clipboard.writeText(webhookUrl).then(() => {
        const btn = document.querySelector('.copy-btn');
        const originalText = btn.textContent;
        btn.textContent = '✅ Скопировано';
        setTimeout(() => {
          btn.textContent = originalText;
        }, 2000);
      }).catch(err => {
        console.error('Ошибка копирования:', err);
        alert('Не удалось скопировать URL');
      });
    };

    async function load() {
      try {
        const r = await fetch(`./api/config/${enterprise}`);
        const j = await r.json();
        const cfg = (j||{});
        
        // Загружаем значения из БД и устанавливаем в поля
        document.getElementById('integrationCode').value = cfg.integration_code || '';
        document.getElementById('apiToken').value = cfg.api_token || '';
        document.getElementById('enabled').checked = !!cfg.enabled;
        
        // Обновляем webhook URL - теперь он генерируется на сервере с UUID
        const webhookUrl = cfg.webhook_url || `https://bot.vochi.by/ms/webhook/${enterprise}`;
        document.getElementById('webhookUrl').textContent = webhookUrl;
        
        // Загружаем настройки уведомлений
        const notifications = cfg.notifications || {};
        const callModeNone = document.querySelector('input[name="callNotifyMode"][value="none"]');
        const callModeDuring = document.querySelector('input[name="callNotifyMode"][value="during"]');
        const notifyIncoming = document.getElementById('notifyIncoming');
        const notifyOutgoing = document.getElementById('notifyOutgoing');
        
        if (callModeNone && callModeDuring) {
          const mode = notifications.call_notify_mode || 'during';
          callModeNone.checked = (mode === 'none');
          callModeDuring.checked = (mode === 'during');
        }
        if (notifyIncoming) {
          notifyIncoming.checked = notifications.notify_incoming !== false;
        }
        if (notifyOutgoing) {
          notifyOutgoing.checked = !!notifications.notify_outgoing;
        }
        
        // Загружаем настройки действий при входящем звонке
        const actions = cfg.incoming_call_actions || {};
        const createClientOnCall = document.getElementById('createClientOnCall');
        const createOrderNone = document.querySelector('input[name="createOrder"][value="none"]');
        const createOrderAlways = document.querySelector('input[name="createOrder"][value="always"]');
        const orderSource = document.getElementById('orderSource');
        
        if (createClientOnCall) {
          createClientOnCall.checked = actions.create_client !== false;
        }
        
        if (createOrderNone && createOrderAlways) {
          const createOrderMode = actions.create_order || 'none';
          createOrderNone.checked = (createOrderMode === 'none');
          createOrderAlways.checked = (createOrderMode === 'always');
        }
        
        if (orderSource) {
          orderSource.value = actions.order_source || 'Телефонный звонок';
        }
        
        // Загружаем настройки действий при исходящем звонке
        const outgoingActions = cfg.outgoing_call_actions || {};
        const createClientOnOutgoing = document.getElementById('createClientOnOutgoing');
        const createOrderOutgoingNone = document.querySelector('input[name="createOrderOutgoing"][value="none"]');
        const createOrderOutgoingAlways = document.querySelector('input[name="createOrderOutgoing"][value="always"]');
        const orderSourceOutgoing = document.getElementById('orderSourceOutgoing');
        
        if (createClientOnOutgoing) {
          createClientOnOutgoing.checked = outgoingActions.create_client !== false;
        }
        
        if (createOrderOutgoingNone && createOrderOutgoingAlways) {
          const createOrderOutgoingMode = outgoingActions.create_order || 'none';
          createOrderOutgoingNone.checked = (createOrderOutgoingMode === 'none');
          createOrderOutgoingAlways.checked = (createOrderOutgoingMode === 'always');
        }
        
        if (orderSourceOutgoing) {
          orderSourceOutgoing.value = outgoingActions.order_source || 'Исходящий звонок';
        }
        
        console.log('✅ Конфигурация загружена:', cfg);
      } catch(e) { 
        console.warn('load() error', e); 
      }
    }

    async function save() {
      // Убедимся, что сотрудники загружены перед сохранением
      const employeesList = document.getElementById('employeesList');
      if (employeesList && employeesList.children.length === 0) {
        console.log('Employees not loaded yet, loading...');
        await loadEmployees();
      }
      const integrationCode = document.getElementById('integrationCode').value?.trim() || '';
      const apiToken = document.getElementById('apiToken').value?.trim() || '';
      const enabled = !!document.getElementById('enabled').checked;
      
      // Собираем настройки уведомлений
      const callModeNone = document.querySelector('input[name="callNotifyMode"][value="none"]');
      const callModeDuring = document.querySelector('input[name="callNotifyMode"][value="during"]');
      const notifyIncoming = document.getElementById('notifyIncoming');
      const notifyOutgoing = document.getElementById('notifyOutgoing');
      
      const notifications = {
        call_notify_mode: (callModeNone && callModeNone.checked) ? 'none' : 'during',
        notify_incoming: !!(notifyIncoming && notifyIncoming.checked),
        notify_outgoing: !!(notifyOutgoing && notifyOutgoing.checked)
      };
      
      // Собираем настройки действий при входящем звонке
      const createClientOnCall = document.getElementById('createClientOnCall');
      const createOrderNone = document.querySelector('input[name="createOrder"][value="none"]');
      const createOrderAlways = document.querySelector('input[name="createOrder"][value="always"]');
      const orderSource = document.getElementById('orderSource');
      
      let createOrderMode = 'none';
      if (createOrderAlways && createOrderAlways.checked) createOrderMode = 'always';
      
      const incoming_call_actions = {
        create_client: !!(createClientOnCall && createClientOnCall.checked),
        create_order: createOrderMode,
        order_source: (orderSource && orderSource.value) || 'Телефонный звонок'
      };
      
      // Собираем настройки действий при исходящем звонке
      const createClientOnOutgoing = document.getElementById('createClientOnOutgoing');
      const createOrderOutgoingNone = document.querySelector('input[name="createOrderOutgoing"][value="none"]');
      const createOrderOutgoingAlways = document.querySelector('input[name="createOrderOutgoing"][value="always"]');
      const orderSourceOutgoing = document.getElementById('orderSourceOutgoing');
      
      let createOrderOutgoingMode = 'none';
      if (createOrderOutgoingAlways && createOrderOutgoingAlways.checked) createOrderOutgoingMode = 'always';
      
      const outgoing_call_actions = {
        create_client: !!(createClientOnOutgoing && createClientOnOutgoing.checked),
        create_order: createOrderOutgoingMode,
        order_source: (orderSourceOutgoing && orderSourceOutgoing.value) || 'Исходящий звонок'
      };
      
      // Собираем данные о сотрудниках для сохранения соответствий ID ↔ extension
      const employeeMapping = {};
      if (employeesList) {
        const employees = employeesList.querySelectorAll('.employee-item');
        console.log('Found employee elements:', employees.length);
        employees.forEach(emp => {
          const employeeId = emp.dataset.employeeId;
          const extension = emp.dataset.extension;
          const name = emp.dataset.name;
          const email = emp.dataset.email;
          console.log('Processing employee:', {employeeId, extension, name, email});
          if (employeeId && extension) {
            employeeMapping[extension] = {
              employee_id: employeeId,
              name: name || '',
              email: email || ''
            };
          }
        });
      }
      console.log('Final employee mapping:', employeeMapping);
      
      const btn = document.getElementById('saveBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        let r = await fetch(`./api/config/${enterprise}`, { 
          method:'PUT', 
          headers:{'Content-Type':'application/json'}, 
          body: JSON.stringify({
            phone_api_url: 'https://api.moysklad.ru/api/phone/1.0',
            integration_code: integrationCode,
            api_token: apiToken,
            enabled: enabled,
            notifications: notifications,
            incoming_call_actions: incoming_call_actions,
            outgoing_call_actions: outgoing_call_actions,
            employee_mapping: employeeMapping
          }) 
        });
        const jr = await r.json();
        if(!jr.success) throw new Error(jr.error||'Ошибка сохранения');
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
        document.getElementById('integrationCode').value = '';
        document.getElementById('apiToken').value = '';
        document.getElementById('enabled').checked = false;
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }

    // Глобальная функция для тестирования менеджера
    window.testManager = async function(employeeId, extension, name) {
      console.log('🧪 Testing manager:', {employeeId, extension, name});
      
      const msg = document.getElementById('msg');
      if (msg) { 
        msg.textContent = `🧪 Отправляем тестовый звонок для ${name} (внутр. ${extension})...`;
        msg.className = 'hint';
      }
      
      try {
        const response = await fetch(`./api/test-manager/${enterprise}`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            employee_id: employeeId,
            extension: extension,
            name: name
          })
        });
        
        const result = await response.json();
        
        if (result.success) {
          if (msg) {
            msg.textContent = `✅ Тестовый звонок отправлен для ${name}! Проверьте кабинет МойСклад.`;
            msg.className = 'hint success';
          }
        } else {
          if (msg) {
            msg.textContent = `❌ Ошибка тестирования: ${result.error}`;
            msg.className = 'hint error';
          }
        }
      } catch(error) {
        console.error('Test manager error:', error);
        if (msg) {
          msg.textContent = `❌ Ошибка соединения: ${error.message}`;
          msg.className = 'hint error';
        }
      }
    };

    function openJournal() {
      const url = `./journal?enterprise_number=${enterprise}`;
      window.open(url, '_blank');
    }

    // События
    const saveBtn = document.getElementById('saveBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const journalBtn = document.getElementById('journalBtn');
    
    if (saveBtn) saveBtn.addEventListener('click', save);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteIntegration);
    if (journalBtn) journalBtn.addEventListener('click', openJournal);

    // Функции для работы с сотрудниками
    async function loadEmployees() {
      const loadBtn = document.getElementById('loadEmployeesBtn');
      const status = document.getElementById('employeesStatus');
      const container = document.getElementById('employeesContainer');
      const list = document.getElementById('employeesList');
      const total = document.getElementById('employeesTotal');
      const apiStatus = document.getElementById('employeesApiStatus');
      
      try {
        loadBtn.disabled = true;
        loadBtn.innerHTML = '⏳ Загрузка...';
        status.textContent = 'Загружаем данные сотрудников...';
        
        const response = await fetch(`/ms-admin/api/employees/${enterprise}`);
        const data = await response.json();
        
        if (data.success) {
          const employees = data.employees || [];
          
          // Очищаем список
          list.innerHTML = '';
          
          if (employees.length === 0) {
            list.innerHTML = '<div style="color: #a8c0e0; text-align: center; padding: 16px;">Сотрудники не найдены</div>';
          } else {
            employees.forEach(emp => {
              console.log('Creating element for employee:', emp);
              const row = document.createElement('div');
              row.className = 'employee-item';
              row.style.cssText = 'display: grid; grid-template-columns: 3fr 1fr 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid #374151; align-items: center;';
              
              // Добавляем data-атрибуты для сохранения соответствий
              if (emp.id) row.dataset.employeeId = emp.id;
              if (emp.extension) row.dataset.extension = emp.extension;
              if (emp.name) row.dataset.name = emp.name;
              if (emp.email) row.dataset.email = emp.email;
              console.log('Added data attributes:', {employeeId: emp.id, extension: emp.extension, name: emp.name, email: emp.email});
              
              const extensionStyle = emp.has_extension ? 
                'background: #065f46; color: #10b981; padding: 2px 8px; border-radius: 4px; text-align: center; font-weight: bold;' :
                'color: #6b7280; text-align: center;';
              
              // Кнопка тестирования для менеджеров с внутренним номером
              const testButtonHtml = emp.has_extension ? 
                `<button type="button" 
                   onclick="testManager('${emp.id}', '${emp.extension}', '${emp.name}')" 
                   style="background: #059669; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px;"
                   title="Тестовый звонок для ${emp.name}">
                  🧪 Тест
                </button>` :
                '<span style="color: #6b7280; text-align: center;">—</span>';
              
              row.innerHTML = `
                <div style="color: #ffffff;">${emp.name}</div>
                <div style="${extensionStyle}">${emp.extension || '—'}</div>
                <div style="text-align: center;">${testButtonHtml}</div>
              `;
              list.appendChild(row);
            });
          }
          
          // Показываем контейнер
          container.style.display = 'block';
          
          // Обновляем статистику
          const withExtension = employees.filter(e => e.has_extension).length;
          total.textContent = `Всего сотрудников: ${employees.length}, с внутренними номерами: ${withExtension}`;
          
          const apiInfo = [];
          if (data.phone_api_available) apiInfo.push('Phone API: ✅');
          if (data.main_api_available) apiInfo.push('Основной API: ✅');
          apiStatus.textContent = apiInfo.join(' | ');
          
          status.textContent = `Загружено ${employees.length} сотрудников`;
          status.style.color = '#10b981';
          
        } else {
          status.textContent = `Ошибка: ${data.error}`;
          status.style.color = '#ef4444';
          container.style.display = 'none';
        }
        
      } catch (error) {
        console.error('Ошибка загрузки сотрудников:', error);
        status.textContent = 'Ошибка соединения с сервером';
        status.style.color = '#ef4444';
        container.style.display = 'none';
      } finally {
        loadBtn.disabled = false;
        loadBtn.innerHTML = '🔄 Загрузить сотрудников';
      }
    }
    
    // Привязываем обработчик кнопки загрузки сотрудников
    document.getElementById('loadEmployeesBtn').addEventListener('click', loadEmployees);

    // Загружаем конфигурацию при открытии страницы
    load();
    // Автоматически загружаем сотрудников при входе  
    loadEmployees();
  } catch(e) { console.error('Main script error:', e); }
  })();
  </script>
</body>
</html>
"""

@app.get("/")
async def root():
    """Основная страница с информацией о сервисе"""
    return {
        "service": "МойСклад Integration Service",
        "version": "1.0.0",
        "port": 8023,
        "api_docs": "https://dev.moysklad.ru/doc/api/remap/1.2/#mojsklad-json-api",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }


# =============================================================================
# ЛОГОТИП И СТАТИЧЕСКИЕ ФАЙЛЫ
# =============================================================================

@app.get("/ms.png")
async def serve_logo():
    """Отдача логотипа МойСклад"""
    import os
    from fastapi.responses import FileResponse
    
    logo_path = "/root/asterisk-webhook/ms.png"
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    else:
        # Возвращаем 404 если файл не найден
        raise HTTPException(status_code=404, detail="Logo not found")

@app.get("/ms-admin/favicon.ico")
async def serve_favicon():
    """Отдача favicon для админки"""
    import os
    from fastapi.responses import FileResponse
    
    # Используем общий favicon системы
    favicon_path = "/root/asterisk-webhook/app/static/favicon.ico"
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/x-icon")
    else:
        raise HTTPException(status_code=404, detail="Favicon not found")

# =============================================================================
# АДМИНКА ЭНДПОИНТЫ
# =============================================================================

@app.get("/ms-admin/")
async def ms_admin_page(enterprise_number: str):
    """Админка МойСклад интеграции для предприятия"""
    import asyncpg
    
    # Получаем имя предприятия из БД
    enterprise_name = "Предприятие"
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres", 
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        row = await conn.fetchrow(
            "SELECT name FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        if row:
            enterprise_name = row["name"]
            
        await conn.close()
    except Exception as e:
        logger.error(f"Failed to get enterprise name: {e}")
    
    # Подставляем имя предприятия в HTML
    html_content = MS_ADMIN_HTML.replace("{enterprise_name}", enterprise_name).replace("{enterprise_number}", enterprise_number)
    
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_content)

@app.get("/ms-admin/api/config/{enterprise_number}")
async def ms_admin_api_get_config(enterprise_number: str):
    """Получение конфигурации МойСклад для предприятия"""
    try:
        import asyncpg, json
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Получаем конфигурацию из БД
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        await conn.close()
        
        cfg: dict = {}
        if row and row.get("integrations_config") is not None:
            raw_cfg = row["integrations_config"]
            if isinstance(raw_cfg, str):
                try:
                    cfg = json.loads(raw_cfg) or {}
                except Exception:
                    cfg = {}
            elif isinstance(raw_cfg, dict):
                cfg = raw_cfg
            else:
                # На всякий случай пробуем привести к словарю
                try:
                    cfg = dict(raw_cfg)
                except Exception:
                    cfg = {}

        ms_config = (cfg.get("ms") if isinstance(cfg, dict) else None) or {}
        notifications = ms_config.get("notifications", {})
        incoming_call_actions = ms_config.get("incoming_call_actions", {})
        outgoing_call_actions = ms_config.get("outgoing_call_actions", {})

        # Генерируем webhook URL с UUID для безопасности
        webhook_uuid = ms_config.get("webhook_uuid")
        if not webhook_uuid:
            import uuid
            webhook_uuid = str(uuid.uuid4())
            # Сохраняем UUID обратно в конфигурацию
            ms_config["webhook_uuid"] = webhook_uuid
            current_config["ms"] = ms_config
            
            # Обновляем в БД
            await conn.execute(
                "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
                json.dumps(current_config),
                enterprise_number
            )
        
        webhook_url = f"https://bot.vochi.by/ms/webhook/{webhook_uuid}"
        
        return {
            "phone_api_url": ms_config.get("phone_api_url", "https://api.moysklad.ru/api/phone/1.0"),
            "integration_code": ms_config.get("integration_code", ""),
            "api_token": ms_config.get("api_token", ""),
            "enabled": ms_config.get("enabled", False),
            "webhook_url": webhook_url,
            "notifications": {
                "call_notify_mode": notifications.get("call_notify_mode", "during"),
                "notify_incoming": notifications.get("notify_incoming", True),
                "notify_outgoing": notifications.get("notify_outgoing", False)
            },
            "incoming_call_actions": {
                "create_client": incoming_call_actions.get("create_client", True),
                "create_order": incoming_call_actions.get("create_order", "none"),
                "order_source": incoming_call_actions.get("order_source", "Телефонный звонок")
            },
            "outgoing_call_actions": {
                "create_client": outgoing_call_actions.get("create_client", False),
                "create_order": outgoing_call_actions.get("create_order", "none"),
                "order_source": outgoing_call_actions.get("order_source", "Исходящий звонок")
            },
            "employee_mapping": ms_config.get("employee_mapping", {})
        }
        
    except Exception as e:
        logger.error(f"Error getting MS config: {e}")
        # Генерируем временный UUID для нового enterprise
        import uuid
        webhook_uuid = str(uuid.uuid4())
        webhook_url = f"https://bot.vochi.by/ms/webhook/{webhook_uuid}"
        
        return {
            "phone_api_url": "https://api.moysklad.ru/api/phone/1.0",
            "integration_code": "",
            "api_token": "",
            "enabled": False,
            "webhook_url": webhook_url,
            "notifications": {
                "call_notify_mode": "during",
                "notify_incoming": True,
                "notify_outgoing": False
            },
            "incoming_call_actions": {
                "create_client": True,
                "create_order": "none",
                "order_source": "Телефонный звонок"
            },
            "outgoing_call_actions": {
                "create_client": False,
                "create_order": "none",
                "order_source": "Исходящий звонок"
            }
        }

@app.put("/ms-admin/api/config/{enterprise_number}")
async def ms_admin_api_put_config(enterprise_number: str, request: Request):
    """Сохранение конфигурации МойСклад для предприятия"""
    try:
        import asyncpg, json
        
        body = await request.json()
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Получаем текущую конфигурацию
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        current_config = {}
        if row and row.get("integrations_config"):
            raw_cfg = row["integrations_config"]
            if isinstance(raw_cfg, str):
                try:
                    current_config = json.loads(raw_cfg)
                except Exception:
                    current_config = {}
            elif isinstance(raw_cfg, dict):
                current_config = raw_cfg

        # Обновляем секцию ms
        if "ms" not in current_config:
            current_config["ms"] = {}
            
        # Генерируем UUID для webhook, если его нет
        existing_ms_config = current_config.get("ms", {})
        webhook_uuid = existing_ms_config.get("webhook_uuid")
        if not webhook_uuid:
            import uuid
            webhook_uuid = str(uuid.uuid4())
        
        ms_config = {
            "phone_api_url": body.get("phone_api_url", "https://api.moysklad.ru/api/phone/1.0"),
            "integration_code": body.get("integration_code", ""),
            "api_token": body.get("api_token", ""),
            "enabled": bool(body.get("enabled", False)),
            "webhook_uuid": webhook_uuid,
            "notifications": body.get("notifications", {}),
            "incoming_call_actions": body.get("incoming_call_actions", {}),
            "outgoing_call_actions": body.get("outgoing_call_actions", {}),
            "employee_mapping": body.get("employee_mapping", {})
        }
        
        current_config["ms"] = ms_config
        
        # Сохраняем в БД
        await conn.execute(
            "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
            json.dumps(current_config), enterprise_number
        )
        
        await conn.close()
        
        logger.info(f"MS config saved for enterprise {enterprise_number}")
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Error saving MS config: {e}")
        return {"success": False, "error": str(e)}

@app.delete("/ms-admin/api/config/{enterprise_number}")
async def ms_admin_api_delete_config(enterprise_number: str):
    """Удаление конфигурации МойСклад для предприятия"""
    try:
        import asyncpg, json
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Получаем текущую конфигурацию
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        current_config = {}
        if row and row.get("integrations_config"):
            raw_cfg = row["integrations_config"]
            if isinstance(raw_cfg, str):
                try:
                    current_config = json.loads(raw_cfg)
                except Exception:
                    current_config = {}
            elif isinstance(raw_cfg, dict):
                current_config = raw_cfg

        # Удаляем секцию ms
        if "ms" in current_config:
            del current_config["ms"]
        
        # Сохраняем в БД
        await conn.execute(
            "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
            json.dumps(current_config), enterprise_number
        )
        
        await conn.close()
        
        logger.info(f"MS config deleted for enterprise {enterprise_number}")
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Error deleting MS config: {e}")
        return {"success": False, "error": str(e)}

@app.get("/ms-admin/api/test/{enterprise_number}")
async def ms_admin_api_test(enterprise_number: str):
    """Тестирование подключения к МойСклад для предприятия"""
    try:
        import asyncpg, json
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Получаем конфигурацию из БД
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        await conn.close()
        
        if not row or not row.get("integrations_config"):
            return {"success": False, "error": "Конфигурация не найдена"}
            
        raw_cfg = row["integrations_config"]
        if isinstance(raw_cfg, str):
            try:
                cfg = json.loads(raw_cfg)
            except Exception:
                return {"success": False, "error": "Неверный формат конфигурации"}
        else:
            cfg = raw_cfg
            
        ms_config = cfg.get("ms", {})
        if not ms_config.get("enabled"):
            return {"success": False, "error": "МойСклад интеграция отключена"}
            
        integration_code = ms_config.get("integration_code", "")
        api_token = ms_config.get("api_token", "")
        phone_api_url = ms_config.get("phone_api_url", "https://api.moysklad.ru/api/phone/1.0")
        
        # Проверяем наличие хотя бы одного из ключей
        if not integration_code and not api_token:
            return {"success": False, "error": "Не заполнены ключ интеграции и токен"}
        
        import httpx
        timeout = httpx.Timeout(10.0)
        test_results = []
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Тест 1: Phone API (если есть ключ интеграции)
            if integration_code:
                try:
                    headers = {
                        "Lognex-Phone-Auth-Token": integration_code,
                        "Accept-Encoding": "gzip",
                        "Content-Type": "application/json"
                    }
                    response = await client.get(f"{phone_api_url}/employee", headers=headers)
                    if response.status_code == 200:
                        data = response.json()
                        employee_count = len(data.get("rows", []))
                        test_results.append(f"✅ Phone API: {employee_count} сотрудников с добавочными")
                    else:
                        test_results.append(f"❌ Phone API: ошибка {response.status_code}")
                except Exception as e:
                    test_results.append(f"❌ Phone API: {str(e)}")
            
            # Тест 2: Основной API (если есть токен)
            if api_token:
                try:
                    headers = {
                        "Authorization": f"Bearer {api_token}",
                        "Accept-Encoding": "gzip", 
                        "Content-Type": "application/json"
                    }
                    response = await client.get("https://api.moysklad.ru/api/remap/1.2/entity/employee", headers=headers)
                    if response.status_code == 200:
                        data = response.json()
                        employee_count = data.get("meta", {}).get("size", 0)
                        test_results.append(f"✅ Основной API: {employee_count} всех сотрудников")
                    else:
                        test_results.append(f"❌ Основной API: ошибка {response.status_code}")
                except Exception as e:
                    test_results.append(f"❌ Основной API: {str(e)}")
            
            # Если есть токен, тестируем поиск контрагентов
            if api_token:
                try:
                    headers = {
                        "Authorization": f"Bearer {api_token}",
                        "Accept-Encoding": "gzip",
                        "Content-Type": "application/json"
                    }
                    response = await client.get("https://api.moysklad.ru/api/remap/1.2/entity/counterparty?limit=5", headers=headers)
                    if response.status_code == 200:
                        data = response.json()
                        counterparty_count = data.get("meta", {}).get("size", 0)
                        test_results.append(f"✅ Поиск контрагентов: {counterparty_count} найдено")
                    else:
                        test_results.append(f"❌ Поиск контрагентов: ошибка {response.status_code}")
                except Exception as e:
                    test_results.append(f"❌ Поиск контрагентов: {str(e)}")
                    
        # Формируем финальный результат
        if test_results:
            return {"success": True, "message": "\n".join(test_results)}
        else:
            return {"success": False, "error": "Не удалось выполнить ни одного теста"}
            
    except Exception as e:
        logger.error(f"Error testing MS connection: {e}")
        return {"success": False, "error": f"Ошибка: {str(e)}"}


@app.get("/ms-admin/api/employees/{enterprise_number}")
async def ms_admin_api_employees(enterprise_number: str):
    """Получение объединенных данных сотрудников из Phone API и основного API"""
    try:
        # Получаем конфигурацию предприятия
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        await conn.close()
            
        if not row or not row['integrations_config']:
            return {"success": False, "error": "Конфигурация предприятия не найдена", "employees": []}
        
        # Парсим JSON если это строка
        integrations_config = row['integrations_config']
        if isinstance(integrations_config, str):
            import json
            try:
                integrations_config = json.loads(integrations_config)
            except json.JSONDecodeError:
                return {"success": False, "error": "Некорректная конфигурация в базе данных", "employees": []}
        
        ms_config = integrations_config.get('ms', {})
        integration_code = ms_config.get("integration_code", "")
        api_token = ms_config.get("api_token", "")
        
        if not integration_code and not api_token:
            return {"success": False, "error": "Не заполнены токены для подключения", "employees": []}
        
        # Получаем объединенные данные сотрудников из Phone API и основного API
        employees_result = []
        phone_employees = {}
        
        try:
            # 1. Сначала получаем добавочные номера из Phone API
            if integration_code:
                async with httpx.AsyncClient() as client:
                    phone_response = await client.get(
                        f"{ms_config.get('phone_api_url', 'https://api.moysklad.ru/api/phone/1.0')}/employee",
                        headers={"Lognex-Phone-Auth-Token": integration_code}
                    )
                    if phone_response.status_code == 200:
                        phone_data = phone_response.json()
                        for emp in phone_data.get("employees", []):
                            employee_id = emp.get("meta", {}).get("href", "").split("/")[-1]
                            phone_employees[employee_id] = emp.get("extention", "")
            
            # 2. Получаем полную информацию о сотрудниках из основного API
            if api_token:
                async with httpx.AsyncClient() as client:
                    main_response = await client.get(
                        "https://api.moysklad.ru/api/remap/1.2/entity/employee",
                        headers={"Authorization": f"Bearer {api_token}"}
                    )
                    if main_response.status_code == 200:
                        main_data = main_response.json()
                        for emp in main_data.get("rows", []):
                            employee_id = emp.get("id")
                            extension = phone_employees.get(employee_id, "")
                            
                            employees_result.append({
                                "id": employee_id,
                                "name": emp.get("name", ""),
                                "email": emp.get("email", ""),
                                "phone": emp.get("phone", ""),
                                "extension": extension,
                                "has_extension": bool(extension)
                            })
        
        except Exception as e:
            logger.error(f"Error fetching employees: {e}")
            return {"success": False, "error": f"Ошибка получения сотрудников: {str(e)}", "employees": []}
        
        return {
            "success": True, 
            "employees": employees_result,
            "total": len(employees_result),
            "phone_api_available": bool(integration_code),
            "main_api_available": bool(api_token)
        }
        
    except Exception as e:
        logger.error(f"Error in ms_admin_api_employees: {e}")
        return {"success": False, "error": f"Ошибка: {str(e)}", "employees": []}

# =============================================================================
# ТЕСТОВЫЕ ЭНДПОИНТЫ
# =============================================================================

@app.get("/test/credentials")
async def test_credentials():
    """Тест подключения к МойСклад API"""
    try:
        # Тест с базовыми учетными данными
        login = MOYSKLAD_CONFIG["login"] or "demo"
        password = MOYSKLAD_CONFIG["password"] or "demo"
        api_url = MOYSKLAD_CONFIG["base_url"]

        org = await get_organization(login, password, api_url)

        if org:
            return {
                "success": True,
                "message": "Подключение к МойСклад успешно",
                "organization": org
            }
        else:
            return {
                "success": False,
                "message": "Не удалось подключиться к МойСклад",
                "error": "Неверные учетные данные или API недоступен"
            }

    except Exception as e:
        return {
            "success": False,
            "message": "Ошибка тестирования подключения",
            "error": str(e)
        }


@app.get("/test/organizations")
async def test_organizations():
    """Тест получения организаций"""
    try:
        login = MOYSKLAD_CONFIG["login"] or "demo"
        password = MOYSKLAD_CONFIG["password"] or "demo"
        api_url = MOYSKLAD_CONFIG["base_url"]

        org = await get_organization(login, password, api_url)

        return {
            "success": org is not None,
            "organization": org
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/ms-admin/api/test-manager/{enterprise_number}")
async def ms_admin_api_test_manager(enterprise_number: str, request: Request):
    """Тестирование конкретного менеджера - полный флоу звонка с записью в МойСклад"""
    try:
        body = await request.json()
        employee_id = body.get("employee_id")
        extension = body.get("extension")
        name = body.get("name", "")
        
        logger.info(f"🧪 Testing manager {name} (ID: {employee_id}, ext: {extension}) for enterprise {enterprise_number}")
        
        # Получаем конфигурацию МойСклад
        ms_config = await get_ms_config_from_cache(enterprise_number)
        if not ms_config:
            return {"success": False, "error": "Конфигурация МойСклад не найдена"}
        
        integration_code = ms_config.get('integration_code')
        phone_api_url = ms_config.get('phone_api_url', 'https://api.moysklad.ru/api/phone/1.0')
        
        if not integration_code:
            return {"success": False, "error": "Ключ интеграции не настроен"}
        
        # Тестовые данные
        test_phone = "+375290000000"
        test_unique_id = f"test-manager-{employee_id}-{int(time.time())}"
        test_comment = f"Тестовое событие для {name}"
        
        # 1. Создаем тестового клиента в МойСклад (если настроено автосоздание)
        contact_info = {}
        incoming_call_actions = ms_config.get('incoming_call_actions', {})
        if incoming_call_actions.get('create_client', False):
            contact_info = await find_or_create_contact(
                phone=test_phone,
                auto_create=True,
                ms_config=ms_config,
                employee_id=employee_id
            )
            logger.info(f"📞 Test contact created/found: {contact_info.get('name', 'Unknown')}")
        
        # 2. Имитируем входящий звонок (dial event) 
        logger.info(f"📞 Simulating incoming call from {test_phone} to extension {extension}")
        
        # Отправляем popup в МойСклад после создания звонка (создается позже)
        
        # 3. Создаем звонок в МойСклад
        call_id = await create_ms_call(
            phone_api_url=phone_api_url,
            integration_code=integration_code,
            caller_phone=test_phone,
            called_extension=extension,
            contact_info=contact_info,
            is_incoming=True
        )
        
        if not call_id:
            return {"success": False, "error": "Не удалось создать звонок в МойСклад"}
        
        logger.info(f"📞 Test call created in МойСклад: {call_id}")
        
        # 3.5. Отправляем popup с созданным call_id
        try:
            await send_ms_popup(
                phone_api_url=phone_api_url,
                integration_code=integration_code,
                call_id=call_id,
                event_type="SHOW",
                extension=extension,
                employee_id=employee_id
            )
            logger.info(f"✅ Popup sent successfully for call {call_id}")
        except Exception as popup_error:
            logger.warning(f"⚠️ Popup failed: {popup_error}")
        
        # 4. Имитируем завершение звонка (hangup event) через небольшую задержку
        await asyncio.sleep(1)  # Небольшая пауза для реалистичности
        
        # Подготавливаем данные hangup события
        hangup_raw = {
            "CallStatus": "2",  # Отвеченный звонок
            "StartTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "EndTime": (datetime.now() + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "Duration": "30",
            "BillSec": "25",
            "Trunk": "test-trunk",
            "Comment": test_comment
        }
        
        # 5. Обновляем звонок с записью и комментарием
        success = await update_ms_call_with_recording(
            phone_api_url=phone_api_url,
            integration_code=integration_code,
            phone=test_phone,
            extension=extension,
            unique_id=test_unique_id,
            record_url="",  # Пустая запись для теста
            call_data={"raw": hangup_raw}
        )
        
        if success:
            logger.info(f"✅ Test call completed successfully for {name}")
            return {
                "success": True,
                "message": f"Тестовый звонок успешно отправлен для {name}",
                "call_id": call_id,
                "contact_created": contact_info.get("found", False),
                "contact_name": contact_info.get("name", "")
            }
        else:
            return {
                "success": False,
                "error": "Звонок создан, но обновление не удалось"
            }
        
    except Exception as e:
        logger.error(f"❌ Test manager error for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/notify-incoming")
async def notify_incoming(request: Request):
    """Уведомление о входящем звонке"""
    try:
        data = await request.json()
        enterprise_number = data.get("enterprise_number", "")
        phone = data.get("phone", "")
        origin = data.get("origin", "live")

        # Получаем конфигурацию предприятия
        config = await get_enterprise_config(enterprise_number)

        if not config.get("enabled", False):
            return {"success": False, "error": "Интеграция отключена"}

        # Логируем событие
        await log_integration_event(
            enterprise_number=enterprise_number,
            event_type="incoming_call",
            request_data=data,
            status="success"
        )

        # Если это recovery mode, пропускаем уведомления
        if origin == "download":
            logger.info(f"Recovery mode: пропускаем уведомления для звонка {phone}")
            return {"success": True, "message": "Recovery mode - уведомления пропущены"}

        # Получаем настройки уведомлений
        notifications = config.get("notifications", {})
        if notifications.get("notify_incoming", False):
            # Здесь будет логика отправки уведомлений
            logger.info(f"Отправка уведомления о входящем звонке: {phone}")
            # TODO: Реализовать отправку уведомлений

        return {"success": True, "message": "Уведомление обработано"}

    except Exception as e:
        logger.error(f"Error in notify_incoming: {e}")
        return {"success": False, "error": str(e)}


@app.post("/log-call")
async def log_call(request: Request):
    """Логирование звонка"""
    try:
        data = await request.json()
        enterprise_number = data.get("enterprise_number", "")
        phone = data.get("phone", "")
        call_type = data.get("type", "incoming")
        origin = data.get("origin", "live")

        # Получаем конфигурацию предприятия
        config = await get_enterprise_config(enterprise_number)

        if not config.get("enabled", False):
            return {"success": False, "error": "Интеграция отключена"}

        # Логируем событие
        await log_integration_event(
            enterprise_number=enterprise_number,
            event_type=f"{call_type}_call_logged",
            request_data=data,
            status="success"
        )

        # Если это recovery mode, пропускаем дополнительные действия
        if origin == "download":
            logger.info(f"Recovery mode: пропускаем дополнительные действия для звонка {phone}")
            return {"success": True, "message": "Recovery mode - дополнительные действия пропущены"}

        # Получаем настройки звонков
        actions = config.get(f"{call_type}_call_actions", {})

        if actions.get("create_order", False):
            # Поиск клиента по телефону
            login = config.get("login", "")
            password = config.get("password", "")
            api_url = config.get("api_url", MOYSKLAD_CONFIG["base_url"])

            customer = await get_customer_by_phone(phone, login, password, api_url)

            if customer:
                # Создаем заказ
                order_data = {
                    "customer_id": customer["id"],
                    "description": f"Звонок от {phone}",
                    "source": actions.get("order_source", "Телефонный звонок")
                }

                order_id = await create_order(order_data, login, password, api_url)

                if order_id:
                    logger.info(f"Создан заказ {order_id} для клиента {customer['id']}")
                else:
                    logger.warning(f"Не удалось создать заказ для клиента {customer['id']}")

        return {"success": True, "message": "Звонок залогирован"}

    except Exception as e:
        logger.error(f"Error in log_call: {e}")
        return {"success": False, "error": str(e)}


@app.post("/customer-by-phone")
async def customer_by_phone(request: Request):
    """Поиск клиента по телефону"""
    try:
        data = await request.json()
        enterprise_number = data.get("enterprise_number", "")
        phone = data.get("phone", "")

        # Получаем конфигурацию предприятия
        config = await get_enterprise_config(enterprise_number)

        if not config.get("enabled", False):
            return {"success": False, "error": "Интеграция отключена"}

        login = config.get("login", "")
        password = config.get("password", "")
        api_url = config.get("api_url", MOYSKLAD_CONFIG["base_url"])

        customer = await get_customer_by_phone(phone, login, password, api_url)

        # Логируем событие
        await log_integration_event(
            enterprise_number=enterprise_number,
            event_type="customer_search",
            request_data={"phone": phone},
            response_data=customer,
            status="success" if customer else "not_found"
        )

        if customer:
            return {
                "success": True,
                "customer": customer,
                "display_name": customer["name"],
                "person_uid": customer["id"]
            }
        else:
            return {
                "success": False,
                "error": "Клиент не найден"
            }

    except Exception as e:
        logger.error(f"Error in customer_by_phone: {e}")
        return {"success": False, "error": str(e)}


@app.post("/responsible-extension")
async def responsible_extension(request: Request):
    """Получение ответственного менеджера для клиента"""
    try:
        data = await request.json()
        enterprise_number = data.get("enterprise_number", "")
        phone = data.get("phone", "")

        # Получаем конфигурацию предприятия
        config = await get_enterprise_config(enterprise_number)

        if not config.get("enabled", False):
            return {"success": False, "error": "Интеграция отключена"}

        login = config.get("login", "")
        password = config.get("password", "")
        api_url = config.get("api_url", MOYSKLAD_CONFIG["base_url"])

        customer = await get_customer_by_phone(phone, login, password, api_url)

        if customer and customer.get("manager_id"):
            # Логируем событие
            await log_integration_event(
                enterprise_number=enterprise_number,
                event_type="responsible_manager",
                request_data={"phone": phone},
                response_data={"manager_id": customer["manager_id"], "manager_name": customer["manager"]},
                status="success"
            )

            return {
                "success": True,
                "manager_id": customer["manager_id"],
                "manager_name": customer["manager"],
                "extension": ""  # Пока не реализовано сопоставление с внутренними номерами
            }
        else:
            return {
                "success": False,
                "error": "Ответственный менеджер не найден"
            }

    except Exception as e:
        logger.error(f"Error in responsible_extension: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# MOYSKLAD PHONE API FUNCTIONS
# =============================================================================

async def find_contact_by_phone(phone: str, api_token: str) -> dict:
    """Поиск контакта по номеру телефона в МойСклад основном API"""
    try:
        logger.info(f"🔍 Searching for contact with phone: {phone}")
        async with httpx.AsyncClient() as client:
            # Ищем контрагентов по номеру телефона
            response = await client.get(
                "https://api.moysklad.ru/api/remap/1.2/entity/counterparty",
                headers={"Authorization": f"Bearer {api_token}"},
                params={"filter": f"phone~{phone}"}
            )
            
            logger.info(f"📞 Contact search response: status={response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"📋 Found {len(data.get('rows', []))} contacts")
                
                if data.get("rows"):
                    contact = data["rows"][0]
                    result = {
                        "found": True,
                        "name": contact.get("name", ""),
                        "phone": contact.get("phone", ""),
                        "email": contact.get("email", ""),
                        "id": contact.get("id", ""),
                        "description": contact.get("description", "")
                    }
                    logger.info(f"✅ Contact found: {result['name']} ({result['phone']})")
                    return result
                else:
                    logger.warning(f"⚠️ No contacts found for phone {phone}")
            else:
                logger.error(f"❌ Contact search failed with status {response.status_code}: {response.text}")
                    
    except Exception as e:
        logger.error(f"❌ Error finding contact by phone {phone}: {e}")
    
    return {"found": False}

async def find_or_create_contact(phone: str, auto_create: bool, ms_config: dict, employee_id: str = None) -> dict:
    """Поиск контакта по телефону или создание нового при необходимости"""
    try:
        # Сначала пытаемся найти существующий контакт
        api_token = ms_config.get('api_token')
        if api_token:
            contact_info = await find_contact_by_phone(phone, api_token)
            if contact_info.get("found"):
                logger.info(f"✅ Existing contact found: {contact_info['name']} ({contact_info['phone']})")
                return contact_info
        
        # Контакт не найден - проверяем настройку автосоздания
        if not auto_create:
            logger.info(f"🔄 Contact not found for {phone}, auto-creation disabled")
            return {"found": False}
        
        # Используем тот же api_token что и для Phone API
        if not api_token:
            logger.warning(f"⚠️ Cannot auto-create contact for {phone}: missing api_token in configuration")
            return {"found": False}
        
        # Создаем нового клиента
        logger.info(f"🆕 Creating new customer for phone: {phone}")
        customer_data = {
            "name": phone,
            "phone": phone,
            "email": "",
            "tags": ["Создан автоматически"]
        }
        
        # Добавляем владельца, если указан
        if employee_id:
            customer_data["owner_id"] = employee_id
            logger.info(f"🔧 Setting customer owner to employee: {employee_id}")
        
        customer_id = await create_customer(customer_data, api_token)
        
        if customer_id:
            logger.info(f"✅ Successfully created customer {customer_id} for phone {phone}")
            # Возвращаем в том же формате что и find_contact_by_phone
            return {
                "found": True,
                "name": customer_data["name"],
                "phone": phone,
                "email": "",
                "id": customer_id,
                "description": "Автоматически созданный клиент",
                "auto_created": True  # Дополнительный флаг
            }
        else:
            logger.error(f"❌ Failed to create customer for phone {phone}")
            return {"found": False}
            
    except Exception as e:
        logger.error(f"❌ Error in find_or_create_contact for {phone}: {e}")
        return {"found": False}

async def create_ms_call(phone_api_url: str, integration_code: str, caller_phone: str, called_extension: str = None, contact_info: dict = {}, is_incoming: bool = True) -> str:
    """Создание звонка в МойСклад Phone API"""
    try:
        # Создаем новый клиент для каждого запроса
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Генерируем уникальный externalId для звонка с номером (extension может быть None)
            import time
            extension_part = called_extension if called_extension else "no-ext"
            external_id = f"webhook-{int(time.time())}-{caller_phone.replace('+', '')}-{extension_part}"
            
            # Используем текущее время в GMT+3 для startTime (МойСклад ожидает местное время)
            from datetime import datetime, timezone, timedelta
            gmt_plus_3 = timezone(timedelta(hours=3))
            current_time_local = datetime.now(gmt_plus_3)
            # Отправляем местное время GMT+3, а НЕ UTC
            current_time = current_time_local.strftime("%Y-%m-%d %H:%M:%S")
            
            call_data = {
                "from": caller_phone,
                "number": caller_phone,  # Номер телефона, а не внутренний номер
                "externalId": external_id,
                "isIncoming": is_incoming,
                "startTime": current_time
                # НЕ указываем extension при создании - будет обновлен при hangup
            }
            
            # Временно отключаем counterparty для отладки
            # if contact_info.get("found") and contact_info.get("id"):
            #     call_data["counterparty"] = {
            #         "meta": {
            #             "href": f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{contact_info.get('id')}",
            #             "type": "counterparty",
            #             "mediaType": "application/json"
            #         }
            #     }
            #     logger.info(f"📋 Creating call with counterparty: {contact_info.get('name')} (ID: {contact_info.get('id')})")
            # else:
            logger.info(f"📋 Creating call without counterparty info for {caller_phone} (debugging)")
            
            logger.info(f"📞 Creating MS call with data: {call_data}")
        
            # Сохраняем call_id для последующего использования в hangup
            import time
            timestamp = int(time.time())
            extension_for_key = called_extension if called_extension else "no-ext"
            call_mapping_key = f"{caller_phone}:{extension_for_key}:{timestamp}"
            
            # Используем POST - МойСклад автоматически найдет контрагента и сотрудника
            response = await client.post(
                f"{phone_api_url}/call",
                headers={"Lognex-Phone-Auth-Token": integration_code},
                json=call_data
            )
        
            logger.info(f"📞 MS call creation response: status={response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                call_id = result.get("id", "")
                logger.info(f"✅ MS call created successfully: {call_id}")
                
                # Сохраняем соответствие в глобальном кэше для всех возможных extensions
                if not hasattr(create_ms_call, 'call_cache'):
                    create_ms_call.call_cache = {}
                
                # Сохраняем основной ключ
                create_ms_call.call_cache[call_mapping_key] = call_id
                logger.info(f"💾 Saved call mapping: {call_mapping_key} -> {call_id}")
                
                # Дополнительно сохраняем ключи для всех возможных extensions (150, 151, 152)
                # чтобы hangup мог найти call_id независимо от того, на какой extension пришел hangup
                phone_clean = caller_phone.replace('+', '')
                for possible_ext in ['150', '151', '152']:
                    additional_key = f"{caller_phone}:{possible_ext}:{timestamp}"
                    create_ms_call.call_cache[additional_key] = call_id
                    logger.debug(f"💾 Saved additional mapping: {additional_key} -> {call_id}")
                
                return call_id
            else:
                logger.error(f"❌ MS call creation failed: {response.status_code} - {response.text}")
                return ""
                
    except Exception as e:
        logger.error(f"Error creating MS call: {e}")
    
    return ""

async def send_ms_popup(phone_api_url: str, integration_code: str, call_id: str, event_type: str, extension: str, employee_id: str) -> bool:
    """Отправка попапа сотруднику в МойСклад"""
    try:
        async with httpx.AsyncClient() as client:
            event_data = {
                "eventType": event_type,
                "extension": extension,
                "sequence": 1
            }
            
            if employee_id:
                event_data["employee"] = {
                    "href": f"https://api.moysklad.ru/api/remap/1.2/entity/employee/{employee_id}",
                    "type": "employee"
                }
            
            response = await client.post(
                f"{phone_api_url}/call/{call_id}/event",
                headers={"Lognex-Phone-Auth-Token": integration_code},
                json=event_data
            )
            
            if response.status_code in [200, 204]:
                logger.info(f"MS popup sent successfully: {event_type} to extension {extension}")
                return True
            else:
                logger.error(f"Failed to send MS popup: {response.status_code} - {response.text}")
                
    except Exception as e:
        logger.error(f"Error sending MS popup: {e}")
    
    return False

async def send_ms_popup_by_external_id(phone_api_url: str, integration_code: str, external_id: str, event_type: str, extension: str, employee_id: str) -> bool:
    """Отправка попапа через externalId (для hangup событий)"""
    try:
        async with httpx.AsyncClient() as client:
            event_data = {
                "eventType": event_type,
                "extension": extension,
                "sequence": 999 if event_type == "HIDE_ALL" else 1
            }
            
            if employee_id:
                event_data["employee"] = {
                    "href": f"https://api.moysklad.ru/api/remap/1.2/entity/employee/{employee_id}",
                    "type": "employee"
                }
            
            response = await client.post(
                f"{phone_api_url}/call/extid/{external_id}/event",
                headers={"Lognex-Phone-Auth-Token": integration_code},
                json=event_data
            )
            
            # 204 - успешно без контента, 200 - успешно с контентом
            if response.status_code in [200, 204]:
                logger.info(f"MS popup sent successfully: {event_type} to extension {extension} (extid: {external_id})")
                return True
            else:
                logger.error(f"MS popup failed: {response.status_code} - {response.text}")
                
    except Exception as e:
        logger.error(f"Error sending MS popup by external_id: {e}")
    
    return False

async def update_ms_call_with_recording(phone_api_url: str, integration_code: str, phone: str, extension: str, unique_id: str, record_url: str, call_data: dict = None):
    """Обновление звонка с прикреплением записи разговора и дополнительными данными"""
    try:
        # Создаем ключ для дедупликации hangup событий
        # Группируем по phone+extension в течение небольшого окна времени (30 сек)
        import time
        current_time = int(time.time())
        time_window = current_time // 30  # 30-секундные окна
        dedup_key = f"{phone}:{extension}:{time_window}"
        
        if dedup_key in processed_hangup_events:
            logger.info(f"⏭️ Hangup event already processed for {dedup_key}, skipping duplicate")
            return
            
        # Проверяем тип канала для фильтрации служебных событий
        raw_data = call_data.get('raw', {}) if call_data else {}
        call_type = raw_data.get('CallType', '')
        trunk = raw_data.get('Trunk', '')
        
        # Фильтруем служебные каналы для исходящих звонков из МойСклад
        if call_type == 2 and not trunk:
            logger.info(f"⏭️ Skipping parasitic incoming channel for outgoing call: CallType={call_type}, Trunk='{trunk}'")
            return
        
        # Добавляем в set обработанных событий
        processed_hangup_events.add(dedup_key)
        logger.info(f"🆕 Processing new hangup event: {dedup_key} (CallType={call_type}, Trunk='{trunk}')")
        
        # Ограничиваем размер set'а (оставляем последние 1000 событий)
        if len(processed_hangup_events) > 1000:
            oldest_events = list(processed_hangup_events)[:100]  # Удаляем первые 100
            for event in oldest_events:
                processed_hangup_events.discard(event)
            logger.info(f"🧹 Cleaned up old hangup events, current size: {len(processed_hangup_events)}")
        # Ищем call_id в кэше - берем самый свежий для данного phone:extension
        phone_without_plus = phone.lstrip('+')
        
        # Для пропущенных звонков (extension пустой) ищем по любому extension
        if not extension or extension.strip() == '':
            cache_key_patterns = [
                f"{phone}:",
                f"{phone_without_plus}:",
                f"+{phone_without_plus}:"
            ]
        else:
            cache_key_patterns = [
                f"{phone}:{extension}:",
                f"{phone_without_plus}:{extension}:",
                f"+{phone_without_plus}:{extension}:"
            ]
        
        call_id = None
        if hasattr(create_ms_call, 'call_cache'):
            # Ищем все подходящие ключи и берем самый свежий
            matching_keys = []
            for key in create_ms_call.call_cache:
                if any(key.startswith(pattern) for pattern in cache_key_patterns):
                    matching_keys.append(key)
            
            if matching_keys:
                # Сортируем по timestamp и берем самый свежий
                latest_key = max(matching_keys, key=lambda k: int(k.split(':')[-1]) if k.split(':')[-1].isdigit() else 0)
                call_id = create_ms_call.call_cache[latest_key]
                logger.info(f"🔍 Found call_id {call_id} for recording update using latest key {latest_key}")
        
        if not call_id:
            logger.warning(f"⚠️ Call ID not found for recording update: {phone} -> {extension}")
            return False
        
        # Подготавливаем данные для обновления
        update_data = {
            "recordUrl": [record_url] if record_url else [],
            "extension": extension  # Устанавливаем правильный extension того, кто ответил
        }
        
        # Обрабатываем дополнительные данные из call_data
        if call_data and call_data.get('raw'):
            raw_data = call_data['raw']
            
            # Рассчитываем длительность из StartTime и EndTime
            start_time = raw_data.get('StartTime')
            end_time = raw_data.get('EndTime')
            if start_time and end_time:
                try:
                    from datetime import datetime, timezone, timedelta
                    
                    # Устанавливаем жестко GMT+3 (Минск/Москва) для нашего сервиса
                    gmt_plus_3 = timezone(timedelta(hours=3))
                    
                    # Парсим времена как GMT+3
                    start_dt_local = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                    end_dt_local = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                    
                    # Добавляем информацию о часовом поясе GMT+3
                    start_dt_tz = start_dt_local.replace(tzinfo=gmt_plus_3)
                    end_dt_tz = end_dt_local.replace(tzinfo=gmt_plus_3)
                    
                    # Конвертируем в UTC для МойСклад API
                    start_dt_utc = start_dt_tz.astimezone(timezone.utc)
                    end_dt_utc = end_dt_tz.astimezone(timezone.utc)
                    
                    # Рассчитываем длительность
                    duration_seconds = int((end_dt_utc - start_dt_utc).total_seconds())
                    
                    # Проверяем, не пытаемся ли мы установить endTime раньше текущего момента
                    # Если да, то это исторические данные - только комментарий, без времени
                    current_utc = datetime.now(timezone.utc)
                    if end_dt_utc <= current_utc:
                        logger.info(f"⏰ Historical call detected: endTime {end_dt_utc} <= current {current_utc}")
                        logger.info(f"📝 Updating only comment and recording, skipping time update")
                        # Не обновляем время для исторических звонков
                    else:
                        # Только для "живых" звонков обновляем время (отправляем GMT+3, а не UTC)
                        update_data["endTime"] = end_dt_tz.strftime("%Y-%m-%d %H:%M:%S.000") 
                        logger.info(f"🌍 GMT+3 endTime for API: {end_dt_tz.strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    logger.info(f"🕐 Call duration calculated: {duration_seconds} seconds")
                    logger.info(f"🌍 Local times (GMT+3): {start_time} - {end_time}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to calculate call duration: {e}")
            
            # Добавляем комментарий на основе CallStatus
            call_status = raw_data.get('CallStatus', '')
            direction = call_data.get('direction', 'in')
            
            # Проверяем, это исходящий звонок по externalId в кэше
            is_outgoing_call = False
            if hasattr(create_ms_call, 'call_cache') and call_id:
                call_cache = create_ms_call.call_cache
                # Ищем external_id среди ключей кэша с этим call_id
                for cached_key, cached_call_id in call_cache.items():
                    if cached_call_id == call_id and 'outgoing-' in cached_key:
                        is_outgoing_call = True
                        direction = 'out'  # Принудительно устанавливаем direction для исходящих
                        logger.info(f"🔄 Detected outgoing call by cache key: {cached_key}")
                        break
            
            comment_parts = []
            
            if call_status == '0':
                if direction == 'in':
                    comment_parts.append("Пропущенный входящий звонок")
                else:
                    comment_parts.append("Неотвеченный исходящий звонок")
            elif call_status == '2':
                if direction == 'in':
                    comment_parts.append("Входящий звонок отвечен")
                else:
                    comment_parts.append("Исходящий звонок отвечен")
            elif call_status == '1':
                comment_parts.append("Звонок занят")
            else:
                comment_parts.append(f"Звонок завершен (статус: {call_status})")
            
            # Добавляем информацию о длительности в комментарий
            if update_data.get("duration"):
                duration_sec = update_data["duration"]  # Теперь уже в секундах
                minutes = duration_sec // 60
                seconds = duration_sec % 60
                if minutes > 0:
                    comment_parts.append(f"Длительность: {minutes} мин {seconds} сек")
                else:
                    comment_parts.append(f"Длительность: {seconds} сек")
            
            comment = " | ".join(comment_parts)
            if comment:
                update_data["comment"] = comment
                logger.info(f"💬 Call comment: {comment}")
        
        # Пытаемся найти externalId из кэша call_id -> externalId
        external_id = None
        if hasattr(create_ms_call, 'call_cache'):
            # Ищем external_id среди ключей кэша
            for cache_key, cached_call_id in create_ms_call.call_cache.items():
                if cached_call_id == call_id:
                    # Извлекаем external_id из cache_key формата: phone:extension:timestamp
                    parts = cache_key.split(':')
                    if len(parts) >= 3:
                        timestamp = parts[-1]
                        phone_clean = phone.replace('+', '')
                        ext_part = extension if extension else "no-ext"
                        external_id = f"webhook-{timestamp}-{phone_clean}-{ext_part}"
                        break
        
        # Используем обычный endpoint по call_id (externalId не работает)
        update_url = f"{phone_api_url}/call/{call_id}"
        logger.info(f"🔧 Updating call {call_id} with data: {update_data}")
        if external_id:
            logger.info(f"🔧 (externalId would be: {external_id})")
        
        async with httpx.AsyncClient() as client:
            response = await client.put(
                update_url,
                headers={"Lognex-Phone-Auth-Token": integration_code},
                json=update_data
            )
            
            if response.status_code in [200, 204]:
                logger.info(f"✅ MS call {call_id} updated with recording: {record_url} and extension: {extension}")
                try:
                    response_data = response.json()
                    logger.info(f"📝 API response: {response_data}")
                except:
                    logger.info(f"📝 API response (non-JSON): {response.text[:200]}")
                return True
            else:
                logger.error(f"❌ Failed to update MS call {call_id} with recording: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"Error updating MS call with recording: {e}")
        return False

async def find_employee_by_extension(phone_api_url: str, integration_code: str, extension: str) -> dict:
    """Поиск сотрудника по добавочному номеру в Phone API"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{phone_api_url}/employee",
                headers={"Lognex-Phone-Auth-Token": integration_code}
            )
            
            if response.status_code == 200:
                data = response.json()
                for emp in data.get("employees", []):
                    if emp.get("extention") == extension:  # Да, с ошибкой "extention"
                        employee_id = emp.get("meta", {}).get("href", "").split("/")[-1]
                        return {
                            "found": True,
                            "id": employee_id,
                            "extension": extension
                        }
                        
    except Exception as e:
        logger.error(f"Error finding employee by extension {extension}: {e}")
    
    return {"found": False}

async def process_ms_incoming_call(phone: str, extension: str, ms_config: dict, enterprise_number: str, unique_id: str, call_data: dict):
    """Обработка входящего звонка для МойСклад - ТОЛЬКО отправка попапов (звонок создается при hangup)"""
    try:
        integration_code = ms_config.get('integration_code')
        api_token = ms_config.get('api_token')
        
        if not integration_code:
            logger.error(f"❌ Missing integration_code for enterprise {enterprise_number}")
            return
        
        # Поиск контакта в МойСклад (с возможностью автосоздания)
        contact_info = {}
        if api_token:
            # Получаем настройку автосоздания клиентов
            incoming_call_actions = ms_config.get('incoming_call_actions', {})
            auto_create = incoming_call_actions.get('create_client', False)
            logger.info(f"🔧 incoming_call_actions: {incoming_call_actions}")
            logger.info(f"🔧 Auto-create setting: {auto_create}")
            
            # Используем новую функцию с автосозданием
            contact_info = await find_or_create_contact(phone, auto_create, ms_config)
        
        # Создание звонка БЕЗ extension (будет обновлен при hangup) + отправка попапов
        phone_api_url = "https://api.moysklad.ru/api/phone/1.0"
        extensions = call_data.get('raw', {}).get('Extensions', [])
        
        if extensions:
            employee_mapping = ms_config.get('employee_mapping', {})
            logger.info(f"🔍 Employee mapping loaded: {employee_mapping}")
            logger.info(f"📋 Processing extensions: {extensions}")
            sent_popups = 0
            
            # Создаем ОДИН звонок БЕЗ указания конкретного extension (будет обновлен при hangup)
            call_id = await create_ms_call(phone_api_url, integration_code, phone, None, contact_info)
            logger.info(f"📞 Created call {call_id} without extension (will be updated on hangup)")
            
            # Отправляем попапы всем сотрудникам с маппингом
            for ext in extensions:
                employee_data = employee_mapping.get(ext)
                
                if employee_data and employee_data.get('employee_id'):
                    employee_id = employee_data['employee_id']
                    employee_name = employee_data.get('name', 'Unknown')
                    
                    # Отправляем попап сотруднику к общему звонку
                    await send_ms_popup(phone_api_url, integration_code, call_id, "SHOW", ext, employee_id)
                    logger.info(f"✅ МойСклад popup sent to extension {ext} ({employee_name}) - extension will be set on hangup")
                    sent_popups += 1
                else:
                    logger.debug(f"🔄 Extension {ext} has no employee mapping - skipping popup")
            
            if sent_popups == 0:
                logger.warning(f"⚠️ No employee mappings found for any extensions {extensions}. Please save employee configuration in admin panel.")
            else:
                logger.info(f"📱 МойСклад popups sent to {sent_popups} employees")
        else:
            logger.warning(f"⚠️ No extensions provided for call")
            
    except Exception as e:
        logger.error(f"❌ Error processing МойСклад incoming call: {e}")

async def process_ms_hangup_call(phone: str, extension: str, ms_config: dict, enterprise_number: str, unique_id: str, record_url: str, call_data: dict):
    """Обработка hangup события для МойСклад - отправка HIDE_ALL и обновление записи"""
    try:
        integration_code = ms_config.get('integration_code')
        
        if not integration_code:
            logger.error(f"❌ Missing integration_code for enterprise {enterprise_number}")
            return
        
        phone_api_url = "https://api.moysklad.ru/api/phone/1.0"
        extensions = call_data.get('raw', {}).get('Extensions', [])
        
        if extensions:
            employee_mapping = ms_config.get('employee_mapping', {})
            sent_hides = 0
            
            for ext in extensions:
                employee_data = employee_mapping.get(ext)
                
                if employee_data and employee_data.get('employee_id'):
                    employee_id = employee_data['employee_id']
                    employee_name = employee_data.get('name', 'Unknown')
                    
                    # Ищем call_id в кэше по номеру телефона, extension и unique_id
                    phone_without_plus = phone.lstrip('+')
                    target_key = None
                    call_id = None
                    
                    if hasattr(create_ms_call, 'call_cache'):
                        # Сначала ищем точное соответствие по unique_id
                        call_timestamp = unique_id.split('.')[0] if '.' in unique_id else unique_id
                        
                        cache_key_patterns = [
                            f"{phone}:{ext}:",
                            f"{phone_without_plus}:{ext}:",
                            f"+{phone_without_plus}:{ext}:"
                        ]
                        
                        # Ищем все подходящие ключи и берем самый свежий (с самым большим timestamp)
                        matching_keys = []
                        for key in create_ms_call.call_cache:
                            if any(key.startswith(pattern) for pattern in cache_key_patterns):
                                matching_keys.append(key)
                        
                        if matching_keys:
                            # Сортируем по timestamp (последняя часть ключа) и берем самый свежий
                            latest_key = max(matching_keys, key=lambda k: int(k.split(':')[-1]) if k.split(':')[-1].isdigit() else 0)
                            call_id = create_ms_call.call_cache[latest_key]
                            logger.info(f"🔍 Found call_id {call_id} for {ext} using latest key {latest_key}")
                        else:
                            logger.warning(f"⚠️ No call_id found for {ext}, phone {phone}")
                    
                    if call_id:
                        # Отправляем HIDE_ALL через call_id
                        success = await send_ms_popup(phone_api_url, integration_code, call_id, "HIDE_ALL", ext, employee_id)
                        if success:
                            logger.info(f"✅ МойСклад HIDE_ALL sent to extension {ext} ({employee_name})")
                            sent_hides += 1
                            # Обновляем звонок с записью и дополнительными данными
                            await update_ms_call_with_recording(phone_api_url, integration_code, phone, ext, unique_id, record_url, call_data)
                        else:
                            logger.error(f"❌ Failed to send HIDE_ALL to extension {ext}")
                    else:
                        logger.warning(f"⚠️ Call ID not found for extension {ext}, phone {phone} - cannot send HIDE_ALL")
                        # Попытка обновить звонок с записью, даже если HIDE_ALL не получилось
                        await update_ms_call_with_recording(phone_api_url, integration_code, phone, ext, unique_id, record_url, call_data)
                        sent_hides += 1  # Считаем как обработанный
                else:
                    logger.debug(f"🔄 Extension {ext} has no employee mapping - skipping HIDE_ALL")
            
            if sent_hides == 0:
                logger.warning(f"⚠️ No HIDE_ALL events sent for extensions {extensions}")
                
                # Для пропущенных звонков (extension пустой) все равно обновляем звонок
                if not extensions or extensions == ['']:
                    logger.info(f"📝 Updating missed call without extension mapping")
                    await update_ms_call_with_recording(phone_api_url, integration_code, phone, '', unique_id, record_url, call_data)
            else:
                logger.info(f"🔄 МойСклад HIDE_ALL sent to {sent_hides} employees")
        else:
            logger.warning(f"⚠️ No extensions provided for hangup call {unique_id}")
            # Обновляем звонок даже если нет extension'ов
            logger.info(f"📝 Updating call without any extensions")
            await update_ms_call_with_recording(phone_api_url, integration_code, phone, '', unique_id, record_url, call_data)
            
    except Exception as e:
        logger.error(f"❌ Error processing МойСклад hangup call: {e}")

async def process_outgoing_call_request(webhook_data: dict, ms_config: dict, enterprise_number: str):
    """Обработка запроса на исходящий звонок из МойСклад (выполняется асинхронно)"""
    try:
        logger.info(f"🚀 Starting background outgoing call processing for enterprise {enterprise_number}")
        # Извлекаем данные webhook
        src_number = webhook_data.get("srcNumber", "")  # Добавочный номер в МойСклад
        dest_number = webhook_data.get("destNumber", "")  # Номер клиента
        uid = webhook_data.get("uid", "")  # UID пользователя МойСклад
        
        logger.info(f"📞 Outgoing call request: {src_number} -> {dest_number} (user: {uid})")
        
        # Валидация данных
        if not src_number or not dest_number:
            logger.error(f"❌ Invalid outgoing call data: srcNumber={src_number}, destNumber={dest_number}")
            return
        
        # Найти внутренний номер Asterisk по srcNumber
        employee_mapping = ms_config.get("employee_mapping", {})
        internal_extension = None
        
        # Ищем в employee_mapping по ключу srcNumber
        if src_number in employee_mapping:
            employee_info = employee_mapping[src_number]
            if isinstance(employee_info, dict):
                # employee_mapping содержит объекты с name, email, employee_id
                internal_extension = src_number  # Используем сам srcNumber как внутренний номер
                logger.info(f"✅ Found employee mapping: {src_number} -> {employee_info.get('name', 'Unknown')}")
            else:
                internal_extension = src_number
        else:
            logger.warning(f"⚠️ No employee mapping found for srcNumber {src_number}, using as-is")
            internal_extension = src_number
        
        logger.info(f"📞 Mapping: МойСклад extension {src_number} -> Asterisk extension {internal_extension}")
        
        # Получаем секрет предприятия для Asterisk API
        import asyncpg
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        try:
            row = await conn.fetchrow(
                "SELECT secret FROM enterprises WHERE number = $1",
                enterprise_number
            )
            
            if not row:
                logger.error(f"❌ Enterprise secret not found for {enterprise_number}")
                return
                
            client_id = row["secret"]
            logger.info(f"🔑 Using enterprise secret: {client_id[:8]}...")
            
        finally:
            await conn.close()
        
        # Инициируем звонок через Asterisk API
        asterisk_result = await call_asterisk_api(
            code=internal_extension,
            phone=dest_number,
            client_id=client_id
        )
        
        if asterisk_result["success"]:
            logger.info(f"✅ Outgoing call initiated successfully: {internal_extension} -> {dest_number}")
            
            # Создаем запись исходящего звонка в МойСклад
            await create_outgoing_call_in_moysklad(
                ms_config=ms_config,
                src_number=src_number,
                dest_number=dest_number,
                uid=uid
            )
            
        else:
            logger.error(f"❌ Failed to initiate outgoing call: {asterisk_result.get('error', 'Unknown error')}")
            
        logger.info(f"🏁 Background outgoing call processing completed for enterprise {enterprise_number}")
            
    except Exception as e:
        logger.error(f"❌ Error in background outgoing call processing for enterprise {enterprise_number}: {e}")
        # В background задаче мы не можем вернуть ошибку пользователю, только логируем

async def call_asterisk_api(code: str, phone: str, client_id: str) -> dict:
    """Вызывает asterisk.py API для инициации звонка"""
    try:
        import aiohttp, json
        
        asterisk_url = "http://localhost:8018/api/makecallexternal"
        params = {
            "code": code,
            "phone": phone,
            "clientId": client_id
        }
        
        logger.info(f"🔗 Calling Asterisk API: {asterisk_url} with params {params}")
        
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

async def create_outgoing_call_in_moysklad(ms_config: dict, src_number: str, dest_number: str, uid: str):
    """Создает запись исходящего звонка в МойСклад"""
    try:
        import time
        
        phone_api_url = ms_config.get("phone_api_url", "https://api.moysklad.ru/api/phone/1.0")
        integration_code = ms_config.get("integration_code", "")
        
        if not integration_code:
            logger.warning(f"⚠️ No integration code for outgoing call creation")
            return
        
        # Генерируем уникальный externalId для исходящего звонка
        external_id = f"outgoing-{int(time.time())}-{dest_number.replace('+', '')}-{src_number}"
        
        # Используем текущее время в GMT+3 для startTime
        from datetime import datetime, timezone, timedelta
        gmt_plus_3 = timezone(timedelta(hours=3))
        current_time_local = datetime.now(gmt_plus_3)
        current_time = current_time_local.strftime("%Y-%m-%d %H:%M:%S")
        
        call_data = {
            "from": dest_number,  # Номер клиента (кому звоним)
            "number": dest_number,
            "externalId": external_id,
            "isIncoming": False,  # Исходящий звонок
            "startTime": current_time,
            "extension": src_number  # Добавочный номер сотрудника
        }
        
        logger.info(f"📞 Creating outgoing MS call: {call_data}")
        
        # Создаем звонок в МойСклад
        async with httpx.AsyncClient(timeout=10) as client:
            headers = {
                "Lognex-Phone-Auth-Token": integration_code,
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip"
            }
            
            resp = await client.post(
                f"{phone_api_url}/call",
                headers=headers,
                json=call_data
            )
            resp.raise_for_status()
            result = resp.json()
            
            call_id = result.get("id")
            logger.info(f"✅ Outgoing MS call created: {call_id}")
            
            # Отправляем popup сотруднику
            await send_ms_popup(
                phone_api_url=phone_api_url,
                integration_code=integration_code,
                call_id=call_id,
                event_type="SHOW",
                extension=src_number,
                employee_id=""  # Не обязательно для исходящих
            )
            
    except Exception as e:
        logger.error(f"❌ Error creating outgoing call in МойСклад: {e}")

async def process_ms_webhook_event(webhook_data: dict, ms_config: dict, enterprise_number: str):
    """Обработка событий webhook от внешних систем (Asterisk) для МойСклад"""
    try:
        # Извлекаем данные о звонке
        event_type = webhook_data.get("event_type", "")
        caller_phone = webhook_data.get("caller_phone", "")
        called_extension = webhook_data.get("called_extension", "")
        
        logger.info(f"Processing MS webhook event: {event_type}, from {caller_phone} to {called_extension}")
        
        if event_type == "call_start" and caller_phone and called_extension:
            # Получаем конфигурацию
            phone_api_url = ms_config.get("phone_api_url", "https://api.moysklad.ru/api/phone/1.0")
            integration_code = ms_config.get("integration_code", "")
            api_token = ms_config.get("api_token", "")
            
            if not integration_code or not api_token:
                logger.warning(f"MS integration not fully configured for enterprise {enterprise_number}")
                return
            
            # 1. Находим сотрудника по добавочному номеру
            employee_info = await find_employee_by_extension(phone_api_url, integration_code, called_extension)
            if not employee_info.get("found"):
                logger.warning(f"Employee not found for extension {called_extension}")
                return
            
            # 2. Ищем контакт по номеру телефона
            contact_info = await find_contact_by_phone(caller_phone, api_token)
            
            # 3. Создаем звонок в МойСклад
            call_id = await create_ms_call(phone_api_url, integration_code, caller_phone, called_extension, contact_info)
            if not call_id:
                logger.error("Failed to create call in MoySklad")
                return
            
            # 4. Отправляем SHOW попап сотруднику
            success = await send_ms_popup(
                phone_api_url, 
                integration_code, 
                call_id, 
                "SHOW", 
                called_extension, 
                employee_info.get("id", "")
            )
            
            if success:
                logger.info(f"Successfully sent popup to extension {called_extension} for call from {caller_phone}")
            else:
                logger.error(f"Failed to send popup to extension {called_extension}")
                
    except Exception as e:
        logger.error(f"Error processing MS webhook event: {e}")

async def process_ms_outgoing_call(phone: str, extension: str, ms_config: dict, enterprise_number: str, unique_id: str, call_data: dict):
    """Обработка исходящего звонка для МойСклад - попап ТОЛЬКО у звонящего сотрудника"""
    try:
        integration_code = ms_config.get('integration_code')
        api_token = ms_config.get('api_token')
        
        if not integration_code:
            logger.error(f"❌ Missing integration_code for enterprise {enterprise_number}")
            return
        
        # Проверяем, включены ли уведомления для исходящих звонков
        notifications = ms_config.get('notifications', {})
        if not notifications.get('notify_outgoing', False):
            logger.info(f"ℹ️ Outgoing call notifications disabled for enterprise {enterprise_number}")
            return
        
        # Определяем ЗВОНЯЩЕГО сотрудника
        raw_data = call_data.get('raw', {})
        caller_id = raw_data.get('CallerIDNum', '')
        
        calling_extension = None
        
        # Пытаемся определить звонящего из CallerIDNum
        if caller_id and caller_id.isdigit():
            calling_extension = caller_id
            logger.info(f"📞 Outgoing call from CallerIDNum: {calling_extension}")
        else:
            # Если CallerIDNum пустой, используем Extensions (обычно один элемент для исходящих)
            extensions = raw_data.get('Extensions', [])
            if len(extensions) == 1:
                calling_extension = extensions[0]
                logger.info(f"📞 Outgoing call from Extensions[0]: {calling_extension}")
            else:
                logger.warning(f"⚠️ Cannot determine calling extension: CallerIDNum='{caller_id}', Extensions={extensions}")
        
        if calling_extension:
            employee_mapping = ms_config.get('employee_mapping', {})
            employee_data = employee_mapping.get(calling_extension)
            
            if employee_data and employee_data.get('employee_id'):
                employee_id = employee_data['employee_id']
                employee_name = employee_data.get('name', 'Unknown')
                
                # Поиск/создание контакта с правильным владельцем
                contact_info = {}
                if api_token:
                    # Получаем настройку автосоздания клиентов для исходящих
                    outgoing_call_actions = ms_config.get('outgoing_call_actions', {})
                    auto_create = outgoing_call_actions.get('create_client', False)
                    logger.info(f"🔧 outgoing_call_actions: {outgoing_call_actions}")
                    logger.info(f"🔧 Auto-create setting for outgoing: {auto_create}")
                    
                    # Используем функцию с автосозданием и правильным employee_id
                    contact_info = await find_or_create_contact(phone, auto_create, ms_config, employee_id)
                
                # Создаем звонок от конкретного сотрудника (исходящий)
                phone_api_url = "https://api.moysklad.ru/api/phone/1.0"
                call_id = await create_ms_call(phone_api_url, integration_code, phone, calling_extension, contact_info, is_incoming=False)
                
                if call_id:
                    # Отправляем попап ТОЛЬКО звонящему сотруднику
                    await send_ms_popup(phone_api_url, integration_code, call_id, "SHOW", calling_extension, employee_id)
                    logger.info(f"✅ МойСклад outgoing popup sent ONLY to calling extension {calling_extension} ({employee_name})")
                else:
                    logger.error(f"❌ Failed to create outgoing call for extension {calling_extension}")
            else:
                logger.warning(f"⚠️ No employee mapping found for calling extension {calling_extension}")
        else:
            logger.warning(f"⚠️ Cannot determine calling extension from call data")
            
    except Exception as e:
        logger.error(f"❌ Error processing МойСклад outgoing call: {e}")

# =============================================================================
# ВНУТРЕННИЕ ЭНДПОИНТЫ ДЛЯ ИНТЕГРАЦИИ
# =============================================================================

@app.post("/internal/ms/incoming-call")
async def internal_ms_call_event(request: Request):
    """Внутренний endpoint для обработки dial событий (входящих и исходящих) от integration_cache"""
    try:
        payload = await request.json()
        logger.info(f"📞 Received incoming call from integration_cache: {payload}")
        
        enterprise_number = payload.get("enterprise_number")
        phone = payload.get("phone")
        extension = payload.get("extension", "")
        direction = payload.get("direction", "in")
        unique_id = payload.get("unique_id")
        
        if not enterprise_number or not phone:
            raise HTTPException(status_code=400, detail="Missing enterprise_number or phone")
        
        # Получить конфигурацию МойСклад для предприятия
        import asyncpg, json
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        try:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if not row:
                logger.error(f"❌ Enterprise {enterprise_number} not found")
                raise HTTPException(status_code=404, detail="Enterprise not found")
            
            integrations_config = row['integrations_config']
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            
            ms_config = integrations_config.get('ms', {})
            if not ms_config.get('enabled'):
                logger.info(f"ℹ️ МойСклад integration not enabled for enterprise {enterprise_number}")
                return {"status": "disabled"}
            
            # Обработать звонок в зависимости от направления
            if direction == "in":
                await process_ms_incoming_call(phone, extension, ms_config, enterprise_number, unique_id, payload)
            elif direction == "out":
                await process_ms_outgoing_call(phone, extension, ms_config, enterprise_number, unique_id, payload)
            
            return {"status": "success"}
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"❌ Error processing incoming call: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/internal/ms/hangup-call")
async def ms_internal_hangup_call(request: Request):
    """Внутренний endpoint для обработки hangup событий от integration_cache"""
    try:
        payload = await request.json()
        logger.info(f"📞 Received hangup event from integration_cache: {payload}")
        
        enterprise_number = payload.get("enterprise_number")
        phone = payload.get("phone")
        extension = payload.get("extension", "")
        direction = payload.get("direction", "in")
        unique_id = payload.get("unique_id")
        record_url = payload.get("record_url")
        
        if not enterprise_number or not phone:
            raise HTTPException(status_code=400, detail="Missing enterprise_number or phone")
        
        # Получить конфигурацию МойСклад для предприятия
        import asyncpg, json
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        try:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if not row:
                logger.error(f"❌ Enterprise {enterprise_number} not found")
                raise HTTPException(status_code=404, detail="Enterprise not found")
            
            integrations_config = row['integrations_config']
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            
            ms_config = integrations_config.get('ms', {})
            if not ms_config.get('enabled'):
                logger.info(f"ℹ️ МойСклад integration not enabled for enterprise {enterprise_number}")
                return {"status": "disabled"}
            
            # Обработать hangup event
            await process_ms_hangup_call(phone, extension, ms_config, enterprise_number, unique_id, record_url, payload)
            
            return {"status": "success"}
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"❌ Error in MS hangup call handler: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# WEBHOOK ЭНДПОИНТЫ ДЛЯ МОЙСКЛАД
# =============================================================================

@app.post("/ms/webhook/{webhook_uuid}")
async def ms_webhook(webhook_uuid: str, request: Request):
    """Webhook эндпоинт для приема запросов от МойСклад"""
    try:
        import asyncpg, json
        
        # Получаем тело запроса
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = json.loads(body.decode('utf-8'))
        else:
            # Если не JSON, пробуем form-data
            form_data = await request.form()
            data = dict(form_data)
        
        logger.info(f"MS webhook received: UUID={webhook_uuid}, data={data}")
        
        # Находим enterprise по webhook_uuid
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg==",
            database="postgres"
        )
        
        try:
            # Ищем enterprise с данным webhook_uuid
            row = await conn.fetchrow(
                "SELECT number, integrations_config FROM enterprises WHERE integrations_config::text LIKE $1",
                f'%"webhook_uuid": "{webhook_uuid}"%'
            )
            
            if not row:
                logger.warning(f"Enterprise not found for webhook UUID: {webhook_uuid}")
                return {"success": False, "error": "Invalid webhook UUID"}
            
            enterprise_number = row['number']
            logger.info(f"Found enterprise {enterprise_number} for webhook UUID {webhook_uuid}")
            
            # Проверяем, что интеграция включена
            config = json.loads(row['integrations_config']) if row['integrations_config'] else {}
            ms_config = config.get("ms", {})
            
            if not ms_config.get("enabled", False):
                logger.warning(f"MoySklad integration disabled for enterprise {enterprise_number}")
                return {"success": False, "error": "Integration disabled"}
            
            # Проверяем тип webhook данных
            if "srcNumber" in data and "destNumber" in data:
                # Это исходящий звонок из МойСклад
                logger.info(f"🔄 Processing outgoing call request from MoySklad")
                
                # Быстро отвечаем МойСклад, обработку делаем в фоне
                import asyncio
                asyncio.create_task(process_outgoing_call_request(data, ms_config, enterprise_number))
                
                logger.info(f"✅ Outgoing call request accepted for enterprise {enterprise_number}")
                return {"success": True, "message": "Call initiation started"}
            else:
                # Старая логика для событий от Asterisk
                await process_ms_webhook_event(data, ms_config, enterprise_number)
                logger.info(f"MS webhook processed successfully for enterprise {enterprise_number}")
                return {"success": True, "message": "Webhook processed"}
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Error processing MS webhook: {e}")
        return {"success": False, "error": str(e)}

# =============================================================================
# SMART.PY INTEGRATION ENDPOINTS
# =============================================================================

def normalize_phone_e164(phone: str) -> str:
    """Нормализует телефонный номер в формат E.164"""
    if not phone:
        return ""
    
    # Удаляем все кроме цифр
    digits = "".join(c for c in phone if c.isdigit())
    
    if not digits:
        return ""
    
    # Если номер начинается с 375 (Беларусь) и имеет 9 цифр
    if len(digits) == 9 and digits.startswith("375"):
        return "+" + digits
    
    # Если номер начинается с 375 и имеет 12 цифр  
    if len(digits) == 12 and digits.startswith("375"):
        return "+" + digits
    
    # Если номер начинается с 7 (Россия) и имеет 11 цифр
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
        
    # Для других случаев добавляем +
    if not phone.startswith("+"):
        return "+" + digits
    
    return phone


async def search_ms_customer(api_token: str, phone_e164: str):
    """Поиск клиента в МойСклад по номеру телефона"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            headers = {
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json;charset=utf-8",
                "Content-Type": "application/json;charset=utf-8"
            }
            
            # Убираем + из номера для поиска
            search_phone = phone_e164.replace("+", "") if phone_e164.startswith("+") else phone_e164
            
            # Поиск контрагентов по номеру телефона
            url = f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty"
            params = {
                "filter": f"phone~{search_phone}",
                "limit": 10
            }
            
            response = await client.get(url, headers=headers, params=params)
            
            if response.status_code == 200:
                data = response.json() or {}
                rows = data.get("rows", [])
                
                if rows:
                    # Возвращаем первый найденный контрагент
                    return {
                        "found": True,
                        "raw": rows[0]
                    }
                else:
                    return {"found": False}
            else:
                logger.warning(f"MS customer search failed: {response.status_code} - {response.text}")
                return {"found": False}
                
    except Exception as e:
        logger.error(f"search_ms_customer error: {e}")
        return {"found": False}


@app.get("/internal/ms/responsible-extension")
async def ms_responsible_extension(phone: str, enterprise_number: Optional[str] = None):
    """
    Возвращает extension ответственного менеджера для номера через МойСклад.
    Аналогично retailcrm и uon интеграциям.
    
    Returns: {"extension": str|null, "manager_id": str|null}
    """
    try:
        # Получаем конфигурацию МойСклад
        import asyncpg
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        if enterprise_number:
            # Получаем конфигурацию МойСклад из БД
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if row and row["integrations_config"]:
                config_data = row["integrations_config"]
                if isinstance(config_data, str):
                    import json
                    config_data = json.loads(config_data)
                config = config_data.get("ms", {}) if config_data else {}
            else:
                config = None
        else:
            # Ищем любое предприятие с активной интеграцией МойСклад
            row = await conn.fetchrow(
                "SELECT number, integrations_config FROM enterprises WHERE active = true "
                "AND integrations_config -> 'ms' ->> 'enabled' = 'true' LIMIT 1"
            )
            if row:
                enterprise_number = row["number"]
                config_data = row["integrations_config"]
                if isinstance(config_data, str):
                    import json
                    config_data = json.loads(config_data)
                config = config_data.get("ms", {}) if config_data else {}
            else:
                config = None
        
        await conn.close()
        
        if not config or not config.get("enabled"):
            return {"extension": None, "manager_id": None}
        
        api_token = config.get("api_token")  # Main API token
        if not api_token:
            return {"extension": None, "manager_id": None}
        
        # Нормализуем номер телефона
        phone_e164 = normalize_phone_e164(phone)
        if not phone_e164:
            return {"extension": None, "manager_id": None}
        
        # Ищем клиента в МойСклад через Main API
        counterparty_data = await search_ms_customer(api_token, phone_e164)
        
        if not counterparty_data or not counterparty_data.get("found"):
            return {"extension": None, "manager_id": None}
        
        # Извлекаем owner (ответственного менеджера) из данных контрагента
        raw_data = counterparty_data.get("raw", {})
        owner_href = None
        
        if isinstance(raw_data, dict):
            owner = raw_data.get("owner")
            if isinstance(owner, dict) and owner.get("meta"):
                owner_href = owner["meta"].get("href")
        
        if not owner_href:
            return {"extension": None, "manager_id": None}
        
        # Извлекаем employee ID из href
        # Пример: https://api.moysklad.ru/api/remap/1.2/entity/employee/b822ef8f-8649-11f0-0a80-14bb00347cf2
        import re
        match = re.search(r'/employee/([a-f0-9-]+)$', owner_href)
        if not match:
            return {"extension": None, "manager_id": None}
        
        employee_id = match.group(1)
        
        # Ищем маппинг employee_id -> extension в конфигурации
        employee_mapping = config.get("employee_mapping", {})
        mapped_extension = None
        
        if isinstance(employee_mapping, dict):
            for ext, employee_data in employee_mapping.items():
                if isinstance(employee_data, dict):
                    emp_id = employee_data.get("employee_id")
                    if emp_id == employee_id:
                        mapped_extension = ext
                        break
                elif isinstance(employee_data, str):
                    # Fallback для простого формата extension -> employee_id
                    if employee_data == employee_id:
                        mapped_extension = ext
                        break
        
        logger.info(f"🔍 MS responsible-extension: phone={phone_e164}, employee_id={employee_id}, extension={mapped_extension}")
        
        return {
            "extension": mapped_extension,
            "manager_id": employee_id
        }
        
    except Exception as e:
        logger.error(f"ms_responsible_extension error: {e}")
        return {"extension": None, "manager_id": None}


@app.get("/internal/ms/customer-name")
async def ms_customer_name(phone: str, enterprise_number: Optional[str] = None):
    """
    Возвращает имя клиента из МойСклад для отображения на телефоне.
    Аналогично retailcrm и uon интеграциям.
    
    Returns: {"name": str|null}
    """
    try:
        if enterprise_number:
            # Получаем конфигурацию МойСклад через cache service с fallback к БД
            config = await get_ms_config_from_cache(enterprise_number)
        else:
            # Если номер предприятия не указан, возвращаем пустой результат
            return {"name": None}
        
        if not config or not config.get("enabled"):
            return {"name": None}
        
        api_token = config.get("api_token")  # Main API token
        if not api_token:
            return {"name": None}
        
        # Нормализуем номер телефона
        phone_e164 = normalize_phone_e164(phone)
        if not phone_e164:
            return {"name": None}
        
        # Ищем клиента в МойСклад через Main API
        counterparty_data = await search_ms_customer(api_token, phone_e164)
        
        if not counterparty_data or not counterparty_data.get("found"):
            return {"name": None}
        
        # Формируем имя для отображения
        raw_data = counterparty_data.get("raw", {})
        display_name = None
        
        if isinstance(raw_data, dict):
            # Приоритет: имя компании -> ФИО контакта
            company_name = raw_data.get("name", "").strip()
            
            # Пытаемся найти ФИО в метаданных или атрибутах
            contact_name = None
            
            # Поиск ФИО в атрибутах
            attributes = raw_data.get("attributes", [])
            if isinstance(attributes, list):
                first_name = None
                last_name = None
                for attr in attributes:
                    if isinstance(attr, dict):
                        attr_name = (attr.get("name") or "").lower()
                        attr_value = attr.get("value", "").strip()
                        if "имя" in attr_name or "first" in attr_name:
                            first_name = attr_value
                        elif "фамилия" in attr_name or "last" in attr_name:
                            last_name = attr_value
                
                if first_name or last_name:
                    contact_name = f"{last_name or ''} {first_name or ''}".strip()
            
            # Используем имя компании или ФИО контакта
            if company_name and company_name != phone_e164:
                display_name = company_name
                if contact_name:
                    display_name = f"{company_name} ({contact_name})"
            elif contact_name:
                display_name = contact_name
        
        logger.info(f"🔍 MS customer-name: phone={phone_e164}, name={display_name}")
        
        return {"name": display_name}
        
    except Exception as e:
        logger.error(f"ms_customer_name error: {e}")
        return {"name": None}


@app.post("/internal/ms/enrich-customer")
async def ms_enrich_customer(phone: str, enterprise_number: Optional[str] = None):
    """
    Endpoint для обогащения БД данными клиента из МойСклад.
    """
    try:
        if not enterprise_number:
            return {"error": "enterprise_number is required"}
            
        result = await enrich_customer_data_from_moysklad(enterprise_number, phone)
        return result
        
    except Exception as e:
        logger.error(f"ms_enrich_customer error: {e}")
        return {"enriched": 0, "skipped": 0, "errors": [str(e)]}

@app.get("/internal/ms/customer-debug")
async def ms_customer_debug(phone: str, enterprise_number: Optional[str] = None):
    """
    DEBUG: Детальный анализ данных клиента из МойСклад.
    Возвращает полную структуру для анализа обогащения БД.
    """
    try:
        if enterprise_number:
            config = await get_ms_config_from_cache(enterprise_number)
        else:
            return {"error": "enterprise_number is required"}
        
        if not config or not config.get("enabled"):
            return {"error": "МойСклад integration not enabled"}
        
        api_token = config.get("api_token")
        if not api_token:
            return {"error": "МойСклад API token not found"}
        
        phone_e164 = normalize_phone_e164(phone)
        logger.info(f"🔍 MS customer DEBUG: phone={phone_e164}")
        
        # Поиск контрагента
        counterparty_data = await search_ms_customer(api_token, phone_e164)
        
        if not counterparty_data or not counterparty_data.get("found"):
            return {"found": False, "phone": phone_e164}
        
        raw_data = counterparty_data.get("raw", {})
        
        # Дополнительно запрашиваем контактные лица
        contactpersons = []
        try:
            counterparty_id = raw_data.get("id")
            if counterparty_id:
                async with httpx.AsyncClient(timeout=5) as client:
                    headers = {
                        "Authorization": f"Bearer {api_token}",
                        "Accept": "application/json;charset=utf-8",
                        "User-Agent": "VochiCRM/1.0"
                    }
                    
                    contacts_url = f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{counterparty_id}/contactpersons"
                    response = await client.get(contacts_url, headers=headers)
                    
                    logger.info(f"🌐 Contacts request: {response.status_code} {response.reason_phrase}")
                    
                    if response.status_code == 200:
                        contacts_data = response.json()
                        contactpersons = contacts_data.get("rows", [])
                        logger.info(f"📞 Found {len(contactpersons)} contact persons")
                    else:
                        logger.warning(f"⚠️ Contacts request failed: {response.status_code}")
                        logger.warning(f"⚠️ Response body: {response.text[:200]}")
        except Exception as e:
            logger.warning(f"⚠️ Error fetching contacts: {e}")
        
        return {
            "found": True,
            "phone": phone_e164,
            "counterparty": {
                "id": raw_data.get("id"),
                "name": raw_data.get("name"),
                "companyType": raw_data.get("companyType"),
                "phone": raw_data.get("phone"),
                "email": raw_data.get("email"),
                "tags": raw_data.get("tags", [])
            },
            "contactpersons": [
                {
                    "id": cp.get("id"),
                    "name": cp.get("name"),
                    "phone": cp.get("phone"),  
                    "email": cp.get("email"),
                    "position": cp.get("position")
                } for cp in contactpersons
            ],
            "raw_structure": {
                "counterparty_keys": list(raw_data.keys()) if raw_data else [],
                "contactperson_keys": [list(cp.keys()) for cp in contactpersons[:1]] if contactpersons else []
            }
        }
        
    except Exception as e:
        logger.error(f"ms_customer_debug error: {e}")
        return {"error": str(e)}

@app.get("/internal/ms/customer-profile")
async def ms_customer_profile(phone: str, enterprise_number: Optional[str] = None):
    """
    Возвращает полный профиль клиента из МойСклад для обогащения БД.
    Аналогично retailcrm и uon интеграциям.
    
    Returns: {
        "last_name": str|null,
        "first_name": str|null, 
        "middle_name": str|null,
        "enterprise_name": str|null,
        "source": {"raw": dict}
    }
    """
    try:
        # Получаем конфигурацию МойСклад
        import asyncpg
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        if enterprise_number:
            # Получаем конфигурацию МойСклад из БД
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if row and row["integrations_config"]:
                config_data = row["integrations_config"]
                if isinstance(config_data, str):
                    import json
                    config_data = json.loads(config_data)
                config = config_data.get("ms", {}) if config_data else {}
            else:
                config = None
        else:
            # Ищем любое предприятие с активной интеграцией МойСклад
            row = await conn.fetchrow(
                "SELECT number, integrations_config FROM enterprises WHERE active = true "
                "AND integrations_config -> 'ms' ->> 'enabled' = 'true' LIMIT 1"
            )
            if row:
                enterprise_number = row["number"]
                config_data = row["integrations_config"]
                if isinstance(config_data, str):
                    import json
                    config_data = json.loads(config_data)
                config = config_data.get("ms", {}) if config_data else {}
            else:
                config = None
        
        await conn.close()
        
        if not config or not config.get("enabled"):
            return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}
        
        api_token = config.get("api_token")  # Main API token
        if not api_token:
            return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}
        
        # Нормализуем номер телефона
        phone_e164 = normalize_phone_e164(phone)
        if not phone_e164:
            return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}
        
        # Ищем клиента в МойСклад через Main API
        counterparty_data = await search_ms_customer(api_token, phone_e164)
        
        if not counterparty_data or not counterparty_data.get("found"):
            return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}
        
        # Извлекаем данные профиля
        raw_data = counterparty_data.get("raw", {})
        profile = {
            "last_name": None,
            "first_name": None,
            "middle_name": None,
            "enterprise_name": None
        }
        
        if isinstance(raw_data, dict):
            # Имя компании
            company_name = raw_data.get("name", "").strip()
            if company_name and company_name != phone_e164:
                profile["enterprise_name"] = company_name
            
            # Поиск ФИО в атрибутах
            attributes = raw_data.get("attributes", [])
            if isinstance(attributes, list):
                for attr in attributes:
                    if isinstance(attr, dict):
                        attr_name = (attr.get("name") or "").lower()
                        attr_value = attr.get("value", "").strip()
                        
                        if ("имя" in attr_name or "first" in attr_name) and not profile["first_name"]:
                            profile["first_name"] = attr_value
                        elif ("фамилия" in attr_name or "last" in attr_name) and not profile["last_name"]:
                            profile["last_name"] = attr_value
                        elif ("отчество" in attr_name or "middle" in attr_name or "patronymic" in attr_name) and not profile["middle_name"]:
                            profile["middle_name"] = attr_value
        
        logger.info(f"🔍 MS customer-profile: phone={phone_e164}, profile={profile}")
        
        # Возвращаем профиль с исходными данными для обогащения
        return {
            **profile,
            "source": {"raw": raw_data}
        }
        
    except Exception as e:
        logger.error(f"ms_customer_profile error: {e}")
        return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}

@app.post("/internal/ms/recovery-call")
async def recovery_call(request: Request):
    """
    Recovery режим: создание клиентов и звонков для пропущенных событий
    БЕЗ попапов, но С созданием всех записей
    """
    try:
        body = await request.json()
        logger.info(f"📦 Recovery call: {body}")
        
        enterprise_number = body.get("enterprise_number")
        phone = body.get("phone")
        extension = body.get("extension", "")
        direction = body.get("direction", "in")
        unique_id = body.get("unique_id")
        record_url = body.get("record_url")
        raw = body.get("raw", {})
        
        if not all([enterprise_number, phone, unique_id]):
            return {"error": "Missing required fields"}
        
        # Получаем конфигурацию МойСклад из БД
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        try:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if not row or not row["integrations_config"]:
                return {"error": "МойСклад not configured"}
                
            config_data = row["integrations_config"]
            if isinstance(config_data, str):
                import json
                config_data = json.loads(config_data)
            
            ms_config = config_data.get("ms", {})
            if not ms_config:
                return {"error": "МойСклад not configured"}
                
            api_token = ms_config.get('api_token')
            if not api_token:
                return {"error": "МойСклад API token not found"}
        finally:
            await conn.close()
        
        logger.info(f"🔄 Recovery: {direction} call {phone} -> {extension}, status: {raw.get('CallStatus')}")
        
        # 1. СОЗДАЕМ КЛИЕНТА (если настроено автосоздание)
        try:
            if direction == "in":
                # Входящий звонок - проверяем incoming_call_actions
                incoming_call_actions = ms_config.get('incoming_call_actions', {})
                auto_create = incoming_call_actions.get('create_client', False)
                logger.info(f"🔧 Incoming auto-create: {auto_create}")
            else:
                # Исходящий звонок - проверяем outgoing_call_actions  
                outgoing_call_actions = ms_config.get('outgoing_call_actions', {})
                auto_create = outgoing_call_actions.get('create_client', False)
                logger.info(f"🔧 Outgoing auto-create: {auto_create}")
            
            if auto_create:
                # Определяем employee_id для исходящих звонков
                employee_id = None
                if direction == "out" and extension:
                    employee_mapping = ms_config.get("employee_mapping", {})
                    for emp_data in employee_mapping:
                        if isinstance(emp_data, dict) and emp_data.get("extension") == extension:
                            employee_id = emp_data.get("employee_id")
                            break
                
                # Создаем/находим клиента
                customer_data = await find_or_create_contact(
                    phone=phone,
                    auto_create=True,
                    ms_config=ms_config,
                    employee_id=employee_id
                )
                logger.info(f"✅ Customer processed: {customer_data.get('name', 'Unknown')}")
            else:
                logger.info(f"ℹ️ Auto-create disabled for {direction} calls")
                
        except Exception as e:
            logger.error(f"Customer creation failed: {e}")
        
        # 2. СОЗДАЕМ ЗВОНОК В МОЙСКЛАД
        try:
            phone_api_url = ms_config.get("phone_api_url", "https://api.moysklad.ru/api/phone/1.0")
            integration_code = ms_config.get("integration_code", "webhook")
            
            # Создаем звонок
            call_id = await create_ms_call(
                phone_api_url=phone_api_url,
                integration_code=integration_code,
                caller_phone=phone,
                called_extension=extension,
                contact_info=customer_data if 'customer_data' in locals() else {},
                is_incoming=(direction == "in")
            )
            
            if call_id:
                logger.info(f"✅ Recovery call created in МойСклад: {call_id}")
                
                # 3. ОБНОВЛЯЕМ С ЗАПИСЬЮ И ВРЕМЕННЫМИ ДАННЫМИ
                if record_url or raw.get("EndTime"):
                    await update_ms_call_with_recording(
                        phone_api_url=phone_api_url,
                        integration_code=integration_code,
                        phone=phone,
                        extension=extension,
                        unique_id=unique_id,
                        record_url=record_url or "",
                        call_data={"raw": raw}
                    )
                    logger.info(f"📝 Recovery call updated with recording")
                
                return {"status": "success", "call_id": call_id, "message": "Recovery call processed"}
            else:
                logger.warning(f"⚠️ Recovery call creation failed")
                return {"status": "warning", "message": "Call creation failed"}
                
        except Exception as e:
            logger.error(f"Recovery call creation error: {e}")
            return {"status": "error", "message": f"Call processing failed: {str(e)}"}
            
    except Exception as e:
        logger.error(f"Recovery call processing error: {e}")
        return {"error": f"Processing failed: {str(e)}"}


# =============================================================================
# УТИЛИТЫ И КЭШИРОВАНИЕ КОНФИГУРАЦИЙ
# =============================================================================

def normalize_phone_e164(phone: str) -> str:
    """Нормализует номер телефона в формат E164."""
    if not phone:
        return phone
    
    # Убираем все символы кроме цифр
    digits = ''.join(c for c in phone if c.isdigit())
    
    # Если номер начинается с 8, заменяем на 7
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    
    # Если номер начинается с 375 (Беларусь) - оставляем как есть
    if digits.startswith('375') and len(digits) == 12:
        return '+' + digits
    
    # Если номер начинается с 7 (Россия) - оставляем как есть 
    if digits.startswith('7') and len(digits) == 11:
        return '+' + digits
    
    # Для остальных случаев добавляем + если его нет
    if not phone.startswith('+'):
        return '+' + digits
    
    return phone

def is_auto_generated_name(name: str, phone: str) -> bool:
    """
    Проверяет, является ли название автосгенерированным (содержит номер телефона).
    """
    if not name or not phone:
        return True
    
    # Убираем все не-цифры из номера для сравнения
    phone_digits = ''.join(c for c in phone if c.isdigit())
    name_digits = ''.join(c for c in name if c.isdigit())
    
    # Если в названии есть цифры номера телефона - это автосгенерированное название
    if phone_digits and phone_digits in name_digits:
        return True
        
    # Если название начинается с + и содержит цифры - тоже автосгенерированное
    if name.startswith('+') and any(c.isdigit() for c in name):
        return True
        
    return False

async def enrich_customer_data_from_moysklad(enterprise_number: str, phone: str) -> Dict[str, Any]:
    """
    Обогащает локальную БД данными клиента из МойСклад.
    
    Алгоритм:
    1. Получает данные контрагента и контактных лиц из МойСклад
    2. Проверяет названия на автогенерацию (содержат номер телефона)
    3. Обогащает last_name и enterprise_name, очищает first_name/middle_name
    4. Связывает все номера контрагента через enterprise_name
    
    Returns: {"enriched": int, "skipped": int, "errors": list}
    """
    enriched_count = 0
    skipped_count = 0
    errors = []
    
    try:
        # Получаем конфигурацию МойСклад
        config = await get_ms_config_from_cache(enterprise_number)
        if not config or not config.get("enabled"):
            return {"enriched": 0, "skipped": 0, "errors": ["МойСклад integration not enabled"]}
        
        api_token = config.get("api_token")
        if not api_token:
            return {"enriched": 0, "skipped": 0, "errors": ["МойСклад API token not found"]}
        
        phone_e164 = normalize_phone_e164(phone)
        logger.info(f"🔍 Enriching customer data for {phone_e164}")
        
        # Поиск контрагента в МойСклад
        counterparty_data = await search_ms_customer(api_token, phone_e164)
        
        if not counterparty_data or not counterparty_data.get("found"):
            logger.info(f"⚠️ Customer not found in МойСклад: {phone_e164}")
            return {"enriched": 0, "skipped": 1, "errors": []}
        
        raw_data = counterparty_data.get("raw", {})
        counterparty_name = raw_data.get("name", "").strip()
        counterparty_id = raw_data.get("id")
        
        # Подключение к БД
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            # 1. Обработка основного контрагента
            if is_auto_generated_name(counterparty_name, phone_e164):
                logger.info(f"⏭️ Skipping auto-generated counterparty name: '{counterparty_name}'")
                skipped_count += 1
            else:
                # Обогащаем основной номер
                await conn.execute("""
                    INSERT INTO customers (enterprise_number, phone_e164, last_name, first_name, middle_name, enterprise_name, meta)
                    VALUES ($1, $2, $3, NULL, NULL, $4, $5)
                    ON CONFLICT (enterprise_number, phone_e164) 
                    DO UPDATE SET 
                        last_name = EXCLUDED.last_name,
                        first_name = NULL,
                        middle_name = NULL,
                        enterprise_name = EXCLUDED.enterprise_name,
                        meta = COALESCE(customers.meta, '{}'::jsonb) || EXCLUDED.meta
                """, enterprise_number, phone_e164, counterparty_name, counterparty_name, 
                json.dumps({"moysklad_counterparty_id": counterparty_id, "source": "moysklad", "updated_at": datetime.now().isoformat()}))
                
                logger.info(f"🏢 Enriched counterparty: {phone_e164} → '{counterparty_name}'")
                enriched_count += 1
            
            # 2. Получение и обработка контактных лиц
            contactpersons = []
            try:
                if counterparty_id:
                    async with httpx.AsyncClient(timeout=5) as client:
                        headers = {
                            "Authorization": f"Bearer {api_token}",
                            "Accept": "application/json;charset=utf-8",
                            "User-Agent": "VochiCRM/1.0"
                        }
                        
                        contacts_url = f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{counterparty_id}/contactpersons"
                        response = await client.get(contacts_url, headers=headers)
                        
                        if response.status_code == 200:
                            contacts_data = response.json()
                            contactpersons = contacts_data.get("rows", [])
                            logger.info(f"📞 Found {len(contactpersons)} contact persons")
                        else:
                            logger.warning(f"⚠️ Contacts request failed: {response.status_code}")
            except Exception as e:
                logger.warning(f"⚠️ Error fetching contacts: {e}")
                errors.append(f"Error fetching contacts: {str(e)}")
            
            # 3. Обработка контактных лиц
            for contact in contactpersons:
                contact_name = contact.get("name", "").strip()
                contact_phone = contact.get("phone", "").strip()
                
                if not contact_phone:
                    logger.info(f"⏭️ Skipping contact without phone: '{contact_name}'")
                    continue
                
                # Нормализуем номер контактного лица
                contact_phone_e164 = normalize_phone_e164(contact_phone)
                
                if not contact_name or is_auto_generated_name(contact_name, contact_phone_e164):
                    logger.info(f"⏭️ Skipping auto-generated contact name: '{contact_name}'")
                    skipped_count += 1
                    continue
                
                # Обогащаем номер контактного лица
                await conn.execute("""
                    INSERT INTO customers (enterprise_number, phone_e164, last_name, first_name, middle_name, enterprise_name, meta)
                    VALUES ($1, $2, $3, NULL, NULL, $4, $5)
                    ON CONFLICT (enterprise_number, phone_e164) 
                    DO UPDATE SET 
                        last_name = EXCLUDED.last_name,
                        first_name = NULL,
                        middle_name = NULL,
                        enterprise_name = EXCLUDED.enterprise_name,
                        meta = COALESCE(customers.meta, '{}'::jsonb) || EXCLUDED.meta
                """, enterprise_number, contact_phone_e164, contact_name, counterparty_name,
                json.dumps({"moysklad_contact_id": contact.get("id"), "source": "moysklad", "updated_at": datetime.now().isoformat()}))
                
                logger.info(f"👤 Enriched contact person: {contact_phone_e164} → '{contact_name}' (company: '{counterparty_name}')")
                enriched_count += 1
        
        finally:
            await conn.close()
        
        logger.info(f"🔗 Enrichment completed: {enriched_count} enriched, {skipped_count} skipped")
        return {"enriched": enriched_count, "skipped": skipped_count, "errors": errors}
        
    except Exception as e:
        error_msg = f"Enrichment error: {str(e)}"
        logger.error(f"❌ {error_msg}")
        errors.append(error_msg)
        return {"enriched": enriched_count, "skipped": skipped_count, "errors": errors}

async def get_ms_config_legacy_fallback(enterprise_number: str) -> Optional[Dict[str, Any]]:
    """
    LEGACY: Прямое обращение к БД для получения конфигурации МойСклад.
    Используется как fallback в старых функциях.
    """
    try:
        import asyncpg, json
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        try:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if not row or not row["integrations_config"]:
                return None

            config_data = row["integrations_config"]
            if isinstance(config_data, str):
                config_data = json.loads(config_data)

            ms_config = config_data.get("ms", {})
            return ms_config if ms_config else None
                
        finally:
            await conn.close()
    
    except Exception as e:
        logger.error(f"❌ Legacy DB fallback failed for {enterprise_number}: {e}")
        return None

async def get_ms_config_from_cache(enterprise_number: str) -> Optional[Dict[str, Any]]:
    """
    Получает конфигурацию МойСклад из cache service с локальным кэшированием.
    Приоритет: локальный кэш -> cache service (8020) -> БД
    """
    current_time = time.time()
    
    # 1. Проверяем локальный кэш
    if enterprise_number in ms_config_cache:
        cached_entry = ms_config_cache[enterprise_number]
        if cached_entry["expires"] > current_time:
            logger.debug(f"🎯 MS config from LOCAL cache for {enterprise_number}")
            return cached_entry["config"]
        else:
            # Удаляем просроченную запись
            del ms_config_cache[enterprise_number]
    
    try:
        # 2. Запрашиваем из cache service (8020)
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"http://127.0.0.1:8020/config/{enterprise_number}/ms")
            
            if response.status_code == 200:
                data = response.json()
                ms_config = data.get("config", {})
                
                # Сохраняем в локальный кэш
                ms_config_cache[enterprise_number] = {
                    "config": ms_config,
                    "expires": current_time + MS_CONFIG_CACHE_TTL
                }
                
                logger.info(f"✅ MS config from CACHE service for {enterprise_number}: enabled={ms_config.get('enabled', False)}")
                return ms_config
            
            elif response.status_code == 404:
                logger.warning(f"⚠️ MS integration not configured for {enterprise_number}")
                return None
            
            else:
                logger.warning(f"⚠️ Cache service error {response.status_code} for {enterprise_number}")
                
    except Exception as e:
        logger.warning(f"⚠️ Cache service unavailable for {enterprise_number}: {e}")
    
    # 3. Fallback к БД (временно, пока cache не стабилен)
    try:
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        try:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if not row or not row["integrations_config"]:
                logger.warning(f"⚠️ No integrations config found for {enterprise_number}")
                return None

            config_data = row["integrations_config"]
            if isinstance(config_data, str):
                config_data = json.loads(config_data)

            ms_config = config_data.get("ms", {})
            if ms_config:
                # Сохраняем в локальный кэш
                ms_config_cache[enterprise_number] = {
                    "config": ms_config,
                    "expires": current_time + MS_CONFIG_CACHE_TTL
                }
                
                logger.info(f"🔄 MS config from DATABASE fallback for {enterprise_number}: enabled={ms_config.get('enabled', False)}")
                return ms_config
            else:
                logger.warning(f"⚠️ MS config not found in integrations for {enterprise_number}")
                return None
                
        finally:
            await conn.close()
    
    except Exception as e:
        logger.error(f"❌ Database fallback failed for {enterprise_number}: {e}")
        return None

# =============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "ms:app",
        host="0.0.0.0",
        port=8023,
        reload=True,
        log_level="info"
    )
