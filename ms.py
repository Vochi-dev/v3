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
    login: str,
    password: str,
    api_url: str
) -> Optional[str]:
    """Создание нового клиента"""
    try:
        url = f"{api_url}/entity/counterparty"

        # Формируем данные для создания контрагента
        data = {
            "name": customer_data["name"],
            "phone": customer_data["phone"],
            "email": customer_data.get("email", ""),
            "tags": customer_data.get("tags", [])
        }

        response = await moy_sklad_request("POST", url, login, password, data)

        if response["success"]:
            return response["data"]["id"]

        return None

    except Exception as e:
        logger.error(f"Error creating customer: {e}")
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
        <button id="testBtn" type="button" class="btn" style="background:#059669;">Тестировать</button>
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
            <div style="display: grid; grid-template-columns: 2fr 2fr 1fr 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid #2d3a52; margin-bottom: 12px; font-weight: bold; color: #a8c0e0;">
              <div>ФИО</div>
              <div>Email</div>
              <div>Телефон</div>
              <div>Внутренний номер</div>
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
        
        console.log('✅ Конфигурация загружена:', cfg);
      } catch(e) { 
        console.warn('load() error', e); 
      }
    }

    async function save() {
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
            incoming_call_actions: incoming_call_actions
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

    async function test() {
      const btn = document.getElementById('testBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        const r = await fetch(`./api/test/${enterprise}`, { method:'POST', headers:{'Content-Type':'application/json'} });
        const jr = await r.json();
        if (jr.success) {
          if (msg) { msg.textContent=`✅ Подключение работает!`; msg.className='hint success'; }
        } else {
          if (msg) { msg.textContent=`❌ ${jr.error}`; msg.className='hint error'; }
        }
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка теста: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }

    function openJournal() {
      const url = `./journal?enterprise_number=${enterprise}`;
      window.open(url, '_blank');
    }

    // События
    const saveBtn = document.getElementById('saveBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const testBtn = document.getElementById('testBtn');
    const journalBtn = document.getElementById('journalBtn');
    
    if (saveBtn) saveBtn.addEventListener('click', save);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteIntegration);
    if (testBtn) testBtn.addEventListener('click', test);
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
              const row = document.createElement('div');
              row.style.cssText = 'display: grid; grid-template-columns: 2fr 2fr 1fr 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid #374151;';
              
              const extensionStyle = emp.has_extension ? 
                'background: #065f46; color: #10b981; padding: 2px 8px; border-radius: 4px; text-align: center; font-weight: bold;' :
                'color: #6b7280; text-align: center;';
              
              row.innerHTML = `
                <div style="color: #ffffff;">${emp.name}</div>
                <div style="color: #a8c0e0;">${emp.email || '—'}</div>
                <div style="color: #a8c0e0;">${emp.phone || '—'}</div>
                <div style="${extensionStyle}">${emp.extension || '—'}</div>
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
            }
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
            "incoming_call_actions": body.get("incoming_call_actions", {})
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
        async with httpx.AsyncClient() as client:
            # Ищем контрагентов по номеру телефона
            response = await client.get(
                "https://api.moysklad.ru/api/remap/1.2/entity/counterparty",
                headers={"Authorization": f"Bearer {api_token}"},
                params={"filter": f"phone~{phone}"}
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("rows"):
                    contact = data["rows"][0]
                    return {
                        "found": True,
                        "name": contact.get("name", ""),
                        "phone": contact.get("phone", ""),
                        "email": contact.get("email", ""),
                        "id": contact.get("id", ""),
                        "description": contact.get("description", "")
                    }
                    
    except Exception as e:
        logger.error(f"Error finding contact by phone {phone}: {e}")
    
    return {"found": False}

async def create_ms_call(phone_api_url: str, integration_code: str, caller_phone: str, called_extension: str, contact_info: dict) -> str:
    """Создание звонка в МойСклад Phone API"""
    try:
        async with httpx.AsyncClient() as client:
            # Генерируем уникальный externalId для звонка
            import time
            external_id = f"webhook-{int(time.time())}-{caller_phone.replace('+', '')}"
            
            call_data = {
                "from": caller_phone,
                "number": called_extension,
                "externalId": external_id,
                "isIncoming": True,
                "startTime": "2025-09-02 12:45:00"
            }
            
            response = await client.post(
                f"{phone_api_url}/call",
                headers={"Lognex-Phone-Auth-Token": integration_code},
                json=call_data
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("id", "")
                
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
            
            # Обрабатываем webhook данные
            await process_ms_webhook_event(data, ms_config, enterprise_number)
            
            logger.info(f"MS webhook processed successfully for enterprise {enterprise_number}")
            return {"success": True, "message": "Webhook processed"}
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Error processing MS webhook: {e}")
        return {"success": False, "error": str(e)}

# =============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "ms:app",
        host="0.0.0.0",
        port=8023,
        reload=True,
        log_level="info"
    )
