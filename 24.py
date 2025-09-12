#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Битрикс24 интеграция - Webhook сервис
Порт: 8024
"""

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

import asyncpg
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bitrix24.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bitrix24 Integration Service", version="1.0.0")

# Глобальные переменные
bitrix24_config_cache = {}
BITRIX24_CONFIG_CACHE_TTL = 300  # 5 минут

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Bitrix24 Integration Service starting on port 8024")

@app.on_event("shutdown") 
async def shutdown_event():
    logger.info("🛑 Bitrix24 Integration Service shutting down")

@app.get("/")
async def root():
    return {
        "service": "Bitrix24 Integration",
        "version": "1.0.0",
        "port": 8024,
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/bitrix24-crm/{webhook_uuid}")
async def uuid_webhook_handler(webhook_uuid: str, request: Request):
    """UUID-based обработчик вебхуков от Битрикс24"""
    try:
        # Получаем данные
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = await request.json()
        else:
            # Для form-data
            form_data = await request.form()
            data = dict(form_data)
        
        logger.info(f"🎯 Получен вебхук Битрикс24 для UUID: {webhook_uuid}")
        logger.info(f"Event: {data.get('event')}")
        
        # Находим предприятие по UUID
        enterprise_number = await find_enterprise_by_webhook_uuid(webhook_uuid)
        if not enterprise_number:
            logger.warning(f"❌ Предприятие с UUID {webhook_uuid} не найдено")
            raise HTTPException(status_code=404, detail="Webhook UUID not found")
        
        # Получаем конфигурацию Битрикс24
        b24_config = await get_bitrix24_config(enterprise_number)
        if not b24_config:
            logger.warning(f"❌ Конфигурация Битрикс24 для {enterprise_number} не найдена")
            raise HTTPException(status_code=404, detail="Bitrix24 configuration not found")
        
        # Проверяем токен
        expected_token = b24_config.get('webhook_token')
        received_token = data.get('auth', {}).get('application_token')
        
        if not expected_token or expected_token != received_token:
            logger.warning(f"🔒 Неверный токен для UUID {webhook_uuid}")
            raise HTTPException(status_code=401, detail="Invalid application token")
        
        # Обработка события
        event_type = data.get('event')
        logger.info(f"🎯 Processing Bitrix24 event: {event_type} for enterprise {enterprise_number}")
        
        if event_type == 'OnExternalCallStart':
            result = await handle_external_call_start(enterprise_number, data)
        elif event_type == 'OnExternalCallBackStart':
            result = await handle_callback_start(enterprise_number, data)
        else:
            logger.warning(f"⚠️ Unknown Bitrix24 event type: {event_type}")
            result = {"status": "unknown_event", "event_type": event_type}
        
        return {"status": "success", "result": result}
        
    except Exception as e:
        logger.error(f"💥 Error processing UUID webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

@app.post("/bitrix24/webhook/test")
async def test_webhook_handler(request: Request):
    """Тестовый обработчик для получения данных исходящего вебхука от Битрикс24"""
    try:
        # Получаем данные
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = await request.json()
        else:
            # Для form-data
            form_data = await request.form()
            data = dict(form_data)
        
        # Логируем полученные данные
        logger.info("🔥 ТЕСТ: Получен вебхук от Битрикс24:")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Data: {json.dumps(data, ensure_ascii=False, indent=2)}")
        
        # Сохраняем в файл для анализа
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"/tmp/bitrix24_webhook_test_{timestamp}.json", "w", encoding="utf-8") as f:
            json.dump({
                "headers": dict(request.headers),
                "data": data,
                "timestamp": timestamp
            }, f, ensure_ascii=False, indent=2)
        
        # Ищем application_token
        app_token = None
        if isinstance(data, dict):
            auth_data = data.get("auth", {})
            if isinstance(auth_data, dict):
                app_token = auth_data.get("application_token")
        
        if app_token:
            logger.info(f"🎯 APPLICATION_TOKEN найден: {app_token}")
        else:
            logger.warning("⚠️ APPLICATION_TOKEN не найден в данных")
        
        return {"status": "ok", "received": True, "app_token": app_token}
        
    except Exception as e:
        logger.error(f"Ошибка обработки тестового вебхука: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "bitrix24",
        "port": 8024,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/24-admin/favicon.ico")
async def serve_favicon():
    """Отдача favicon для админки"""
    from fastapi.responses import FileResponse
    import os
    
    # Используем общий favicon системы
    favicon_path = "/root/asterisk-webhook/app/static/favicon.ico"
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/x-icon")
    else:
        raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/24-admin/logo.png")
async def serve_logo():
    """Отдача логотипа Битрикс24"""
    from fastapi.responses import FileResponse
    import os
    
    logo_path = "/root/asterisk-webhook/24.png"
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    else:
        raise HTTPException(status_code=404, detail="Logo not found")

@app.get("/24-admin/app.js")
async def get_bitrix24_admin_js():
    """Возвращает JavaScript для админки Битрикс24"""
    js_content = '''
// Битрикс24 админка JavaScript
(function() {
    const enterprise = window.location.pathname.split('/')[2];
    
    // Копирование в буфер
    window.copyToClipboard = function(elementId) {
        const element = document.getElementById(elementId);
        const text = element.value;
        
        navigator.clipboard.writeText(text).then(function() {
            const button = element.nextElementSibling;
            const originalText = button.textContent;
            button.textContent = 'Скопировано!';
            button.style.background = '#28a745';
            
            setTimeout(function() {
                button.textContent = originalText;
                button.style.background = '#1b3350';
            }, 2000);
        }).catch(function(err) {
            console.error('Ошибка копирования: ', err);
            element.select();
            document.execCommand('copy');
        });
    };

    // Сохранение конфигурации
    async function save() {
        const incoming_webhook = (document.getElementById('incoming-webhook') || {}).value?.trim() || '';
        const webhook_token = (document.getElementById('webhook-token') || {}).value?.trim() || '';
        const enabled = !!(document.getElementById('enabled') || {}).checked;
        const btn = document.getElementById('saveBtn');
        const msg = document.getElementById('msg');
        
        if (msg) { msg.textContent = ''; msg.className = 'form-text'; }
        if (btn) btn.disabled = true;
        
        try {
            const formData = new FormData();
            formData.append('incoming_webhook', incoming_webhook);
            formData.append('webhook_token', webhook_token);
            formData.append('enabled', enabled);
            
            const r = await fetch(`/24-admin/${enterprise}/save`, {
                method: 'POST',
                body: formData
            });
            
            const jr = await r.json();
            if (!jr.success) throw new Error(jr.error || 'Ошибка сохранения');
            if (msg) { msg.textContent = 'Сохранено'; msg.className = 'form-text success'; }
            
            // Загружаем пользователей если есть конфигурация
            if (incoming_webhook && webhook_token) {
                loadUsers();
            }
        } catch(e) {
            if (msg) { msg.textContent = 'Ошибка: ' + e.message; msg.className = 'form-text error'; }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    // Удаление интеграции
    async function deleteIntegration() {
        const btn = document.getElementById('deleteBtn');
        const msg = document.getElementById('msg');
        if (!confirm('Вы уверены, что хотите удалить интеграцию? Это действие нельзя отменить.')) return;
        
        if (msg) { msg.textContent = ''; msg.className = 'form-text'; }
        if (btn) btn.disabled = true;
        
        try {
            const r = await fetch(`/24-admin/${enterprise}/delete`, { method: 'POST' });
            const jr = await r.json();
            if (!jr.success) throw new Error(jr.error || 'Ошибка удаления');
            if (msg) { msg.textContent = 'Интеграция удалена'; msg.className = 'form-text success'; }
            
            // Очищаем форму
            const incomingEl = document.getElementById('incoming-webhook');
            const tokenEl = document.getElementById('webhook-token');
            const enabledEl = document.getElementById('enabled');
            if (incomingEl) incomingEl.value = '';
            if (tokenEl) tokenEl.value = '';
            if (enabledEl) enabledEl.checked = false;
            
            // Скрываем блок пользователей
            const usersCard = document.getElementById('usersCard');
            if (usersCard) usersCard.style.display = 'none';
        } catch(e) {
            if (msg) { msg.textContent = 'Ошибка: ' + e.message; msg.className = 'form-text error'; }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    // Отображение пользователей
    function displayUsers(users) {
        const usersCard = document.getElementById('usersCard');
        const usersList = document.getElementById('usersList');
        
        if (!users || users.length === 0) {
            if (usersCard) usersCard.style.display = 'none';
            return;
        }
        
        let html = '';
        users.forEach(user => {
            html += `
                <div style="background: #162332; border: 1px solid #1b3350; border-radius: 8px; padding: 16px; margin-bottom: 12px;">
                    <div style="display: flex; align-items: center; justify-content: space-between;">
                        <div>
                            <div style="font-weight: 500; color: #e7eef8; margin-bottom: 4px;">${user.name || 'Без имени'}</div>
                            <div style="font-size: 12px; color: #a8b3c7;">ID: ${user.id} • ${user.email || 'email не указан'}</div>
                            ${user.current_extension ? `<div style="font-size: 12px; color: #00b4db; margin-top: 2px;">📞 ${user.current_extension}</div>` : ''}
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <select id="extension-${user.id}" style="padding: 6px 8px; border: 1px solid #1b3350; border-radius: 4px; background: #0b1728; color: #e7eef8; font-size: 12px;">
                                <option value="">Без номера</option>
                            </select>
                            <button onclick="saveExtension('${user.id}')" style="padding: 6px 12px; background: #059669; color: white; border: none; border-radius: 4px; font-size: 12px; cursor: pointer;">Сохранить</button>
                        </div>
                    </div>
                </div>
            `;
        });
        
        usersList.innerHTML = html;
        usersCard.style.display = 'block';
        
        // Загружаем внутренние номера и заполняем dropdown'ы
        loadInternalPhones(users);
    }

    // Загрузка внутренних номеров
    async function loadInternalPhones(users) {
        try {
            const r = await fetch(`/24-admin/api/internal-phones/${enterprise}`);
            const phones = await r.json();
            populateExtensionDropdowns(phones, users);
        } catch(e) {
            console.error('Ошибка загрузки номеров:', e);
        }
    }

    // Заполнение dropdown'ов номерами
    function populateExtensionDropdowns(phones, users) {
        users.forEach(user => {
            const select = document.getElementById(`extension-${user.id}`);
            if (!select) return;
            
            // Очищаем и добавляем "Без номера"
            select.innerHTML = '<option value="">Без номера</option>';
            
            phones.forEach(phone => {
                const option = document.createElement('option');
                option.value = phone.extension;
                option.textContent = phone.extension;
                
                // Если номер занят другим пользователем, помечаем
                if (phone.assigned_user_id && phone.assigned_user_id !== user.id) {
                    option.textContent += ` (занят)`;
                    option.style.color = '#f87171';
                }
                
                // Если это текущий номер пользователя
                if (phone.assigned_user_id === user.id) {
                    option.selected = true;
                }
                
                select.appendChild(option);
            });
        });
    }

    // Сохранение назначения номера
    window.saveExtension = async function(userId) {
        const select = document.getElementById(`extension-${userId}`);
        if (!select) return;
        
        const selectedExtension = select.value;
        
        // Собираем все текущие назначения со страницы
        const allAssignments = {};
        document.querySelectorAll('[id^="extension-"]').forEach(sel => {
            const uid = sel.id.replace('extension-', '');
            const ext = sel.value;
            if (ext) {
                allAssignments[uid] = ext;
            }
        });
        
        try {
            const r = await fetch(`/24-admin/api/save-extensions/${enterprise}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(allAssignments)
            });
            
            const result = await r.json();
            if (!result.success) throw new Error(result.error || 'Ошибка сохранения');
            
            // Обновляем отображение пользователей
            loadUsers();
        } catch(e) {
            console.error('Ошибка сохранения номера:', e);
            alert('Ошибка: ' + e.message);
        }
    };

    // Загрузка пользователей
    async function loadUsers() {
        const usersLoading = document.getElementById('usersLoading');
        const usersCard = document.getElementById('usersCard');
        
        if (usersLoading) usersLoading.style.display = 'block';
        
        try {
            const r = await fetch(`/24-admin/api/refresh-managers/${enterprise}`, { method: 'POST' });
            const result = await r.json();
            if (!result.success) throw new Error(result.error || 'Ошибка загрузки пользователей');
            
            displayUsers(result.users || []);
        } catch(e) {
            console.error('Ошибка загрузки пользователей:', e);
            if (usersCard) usersCard.style.display = 'none';
        } finally {
            if (usersLoading) usersLoading.style.display = 'none';
        }
    }

    // Обновление (перезагрузка пользователей)
    function refreshManagers() {
        loadUsers();
    }

    // Привязка событий
    document.addEventListener('DOMContentLoaded', function() {
        const saveBtn = document.getElementById('saveBtn');
        const refreshBtn = document.getElementById('refreshBtn');
        const deleteBtn = document.getElementById('deleteBtn');
        
        if (saveBtn) saveBtn.addEventListener('click', save);
        if (refreshBtn) refreshBtn.addEventListener('click', refreshManagers);
        if (deleteBtn) deleteBtn.addEventListener('click', deleteIntegration);
        
        // Если есть настройки - загружаем пользователей
        const incomingWebhook = document.getElementById('incoming-webhook');
        const webhookToken = document.getElementById('webhook-token');
        if (incomingWebhook && webhookToken && incomingWebhook.value && webhookToken.value) {
            loadUsers();
        }
    });

    // Экспортируем функции в глобальную область
    window.loadUsers = loadUsers;
    window.refreshManagers = refreshManagers;
})();
    '''
    
    return Response(content=js_content, media_type="application/javascript")

@app.post("/bitrix24/webhook/{enterprise_number}")
async def bitrix24_webhook(enterprise_number: str, request: Request):
    """Обработка входящих webhook'ов от Битрикс24"""
    
    try:
        body = await request.json()
        logger.info(f"📥 Bitrix24 webhook from {enterprise_number}: {body}")
        
        # Получение конфигурации
        b24_config = await get_bitrix24_config(enterprise_number)
        if not b24_config or not b24_config.get('enabled'):
            logger.warning(f"❌ Bitrix24 integration not enabled for {enterprise_number}")
            raise HTTPException(status_code=404, detail="Битрикс24 integration not found")
        
        # Аутентификация webhook'а
        if not await authenticate_bitrix24_webhook(b24_config, body, request.headers):
            logger.warning(f"🔒 Unauthorized Bitrix24 webhook from {enterprise_number}")
            raise HTTPException(status_code=401, detail="Unauthorized webhook")
        
        # Обработка события
        event_type = body.get('event')
        logger.info(f"🎯 Processing Bitrix24 event: {event_type}")
        
        if event_type == 'OnExternalCallStart':
            result = await handle_external_call_start(enterprise_number, body)
        elif event_type == 'OnExternalCallBackStart':
            result = await handle_callback_start(enterprise_number, body)
        else:
            logger.warning(f"⚠️ Unknown Bitrix24 event type: {event_type}")
            result = {"status": "unknown_event", "event_type": event_type}
        
        return {"status": "success", "result": result}
        
    except Exception as e:
        logger.error(f"💥 Error processing Bitrix24 webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

async def find_enterprise_by_webhook_uuid(webhook_uuid: str) -> Optional[str]:
    """Поиск предприятия по UUID вебхука"""
    try:
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            # Ищем предприятие где в integrations_config.bitrix24.webhook_uuid = webhook_uuid
            row = await conn.fetchrow("""
                SELECT number FROM enterprises 
                WHERE integrations_config->'bitrix24'->>'webhook_uuid' = $1
            """, webhook_uuid)
            
            if row:
                return row['number']
            else:
                return None
                
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Ошибка поиска предприятия по UUID {webhook_uuid}: {e}")
        return None

async def get_bitrix24_config(enterprise_number: str) -> Optional[Dict[str, Any]]:
    """Получение конфигурации Битрикс24 с приоритетом кеша"""
    
    current_time = time.time()
    
    # 1. Проверяем локальный кеш
    if enterprise_number in bitrix24_config_cache:
        cached_entry = bitrix24_config_cache[enterprise_number]
        if cached_entry["expires"] > current_time:
            logger.debug(f"🎯 Bitrix24 config from LOCAL cache for {enterprise_number}")
            return cached_entry["config"]
        else:
            del bitrix24_config_cache[enterprise_number]
    
    # 2. Проверяем глобальный кеш (integration_cache.py)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"http://127.0.0.1:8020/config/{enterprise_number}/bitrix24")
            if response.status_code == 200:
                data = response.json()
                b24_config = data.get("config", {})
                
                # Сохраняем в локальный кеш
                bitrix24_config_cache[enterprise_number] = {
                    "config": b24_config, 
                    "expires": current_time + BITRIX24_CONFIG_CACHE_TTL
                }
                
                logger.info(f"✅ Bitrix24 config from CACHE service for {enterprise_number}: enabled={b24_config.get('enabled', False)}")
                return b24_config
            elif response.status_code == 404:
                logger.warning(f"⚠️ Bitrix24 integration not configured for {enterprise_number}")
                return None
            else:
                logger.warning(f"⚠️ Cache service error {response.status_code} for {enterprise_number}")
    except Exception as e:
        logger.warning(f"⚠️ Cache service unavailable for {enterprise_number}: {e}")
    
    # 3. Fallback к БД
    return await get_bitrix24_config_from_database(enterprise_number)

async def get_bitrix24_config_from_database(enterprise_number: str) -> Optional[Dict[str, Any]]:
    """Fallback получение конфигурации из БД"""
    
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
            
            if row and row['integrations_config']:
                integrations = row['integrations_config']
                if isinstance(integrations, str):
                    integrations = json.loads(integrations)
                
                b24_config = integrations.get('bitrix24', {})
                logger.info(f"📁 Bitrix24 config from DATABASE for {enterprise_number}: enabled={b24_config.get('enabled', False)}")
                return b24_config
                
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"💥 Database error for {enterprise_number}: {e}")
    
    return {}

async def authenticate_bitrix24_webhook(config: Dict[str, Any], payload: Dict[str, Any], headers: Dict[str, str]) -> bool:
    """Аутентификация входящего webhook'а от Битрикс24"""
    
    # Простая аутентификация по токену
    expected_token = config.get('webhook_incoming_token')
    if not expected_token:
        logger.warning("⚠️ No webhook_incoming_token configured")
        return True  # Разрешаем если токен не настроен (для тестирования)
    
    # Проверяем токен в headers или payload
    auth_token = headers.get('X-Auth-Token') or payload.get('auth_token')
    
    if auth_token == expected_token:
        logger.debug("✅ Webhook authentication successful")
        return True
    else:
        logger.warning(f"❌ Webhook authentication failed: expected {expected_token}, got {auth_token}")
        return False

async def handle_external_call_start(enterprise_number: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Обработка события OnExternalCallStart (исходящий звонок из CRM)"""
    
    user_id = event_data.get('USER_ID')
    phone_number = event_data.get('PHONE_NUMBER')
    crm_entity_type = event_data.get('CRM_ENTITY_TYPE')
    crm_entity_id = event_data.get('CRM_ENTITY_ID')
    call_id = event_data.get('CALL_ID')
    
    logger.info(f"📞 External call start: USER_ID={user_id}, PHONE={phone_number}, CRM={crm_entity_type}:{crm_entity_id}")
    
    # Получаем маппинг пользователей
    b24_config = await get_bitrix24_config(enterprise_number)
    user_mapping = b24_config.get('user_mapping', {})
    
    # Находим extension по USER_ID
    extension = None
    for ext, uid in user_mapping.items():
        if str(uid) == str(user_id):
            extension = ext
            break
    
    if not extension:
        logger.warning(f"⚠️ No extension found for USER_ID {user_id}")
        return {"error": "Extension not found for user", "user_id": user_id}
    
    logger.info(f"🎯 Mapped USER_ID {user_id} → extension {extension}")
    
    # TODO: Вызов Asterisk API для инициации звонка
    # await call_asterisk_api(extension, phone_number, call_id)
    
    return {
        "status": "processed",
        "user_id": user_id,
        "extension": extension,
        "phone_number": phone_number,
        "call_id": call_id,
        "action": "initiate_call"
    }

async def handle_callback_start(enterprise_number: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Обработка события OnExternalCallBackStart (обратный звонок)"""
    
    logger.info(f"🔄 Callback start: {event_data}")
    
    # TODO: Обработка обратного звонка
    
    return {
        "status": "processed",
        "event": "callback_start",
        "data": event_data
    }

@app.post("/internal/bitrix24/send-call-event")
async def send_call_event_to_bitrix24(request: Request):
    """Отправка события звонка в Битрикс24 webhook"""
    
    try:
        body = await request.json()
        enterprise_number = body.get('enterprise_number')
        event_data = body.get('event_data', {})
        
        if not enterprise_number:
            raise HTTPException(status_code=400, detail="enterprise_number required")
        
        logger.info(f"📤 Sending call event to Bitrix24 for {enterprise_number}: {event_data}")
        
        # Получение конфигурации
        b24_config = await get_bitrix24_config(enterprise_number)
        if not b24_config or not b24_config.get('webhook_outgoing_url'):
            logger.warning(f"⚠️ No outgoing webhook URL for {enterprise_number}")
            return {"status": "no_webhook_url", "enterprise_number": enterprise_number}
        
        webhook_url = b24_config['webhook_outgoing_url']
        auth_token = b24_config.get('webhook_outgoing_token')
        
        # Подготовка payload
        payload = {
            "timestamp": datetime.now().isoformat(),
            **event_data
        }
        
        # Подготовка headers
        headers = {"Content-Type": "application/json"}
        if auth_token:
            headers["X-Auth-Token"] = auth_token
        
        # Отправка webhook'а
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=payload, headers=headers)
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully sent webhook to Bitrix24 for {enterprise_number}")
                return {
                    "status": "success",
                    "webhook_url": webhook_url,
                    "response_status": response.status_code
                }
            else:
                logger.error(f"❌ Failed to send webhook to Bitrix24: {response.status_code} {response.text}")
                return {
                    "status": "error",
                    "webhook_url": webhook_url,
                    "response_status": response.status_code,
                    "response_text": response.text
                }
                
    except Exception as e:
        logger.error(f"💥 Error sending webhook to Bitrix24: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send webhook: {str(e)}")

@app.get("/stats")
async def get_stats():
    """Статистика сервиса"""
    
    return {
        "service": "bitrix24",
        "port": 8024,
        "config_cache_size": len(bitrix24_config_cache),
        "uptime": time.time(),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/24-admin/{enterprise_number}")
async def bitrix24_admin_page(enterprise_number: str):
    """Админка Битрикс24 для предприятия"""
    
    try:
        # Получаем информацию о предприятии из БД
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            row = await conn.fetchrow(
                "SELECT name, integrations_config FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                raise HTTPException(status_code=404, detail="Предприятие не найдено")
            
            enterprise_name = row['name'] or f"Предприятие {enterprise_number}"
            
            # Получаем или генерируем UUID для исходящего вебхука
            integrations_config = row['integrations_config'] or {}
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            bitrix24_config = integrations_config.get('bitrix24', {})
            
            # Если UUID еще нет - генерируем новый
            webhook_uuid = bitrix24_config.get('webhook_uuid')
            if not webhook_uuid:
                webhook_uuid = str(uuid.uuid4())
                
            # Текущие значения формы
            incoming_webhook = bitrix24_config.get('incoming_webhook', '')
            webhook_token = bitrix24_config.get('webhook_token', '')
            enabled = bitrix24_config.get('enabled', False)
            
        finally:
            await conn.close()
        
        
        # HTML шаблон админки
        html_content = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{enterprise_name} Bitrix24</title>
    <link rel="icon" href="/24-admin/favicon.ico">
    <style>
        body {{ 
            font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; 
            margin: 0; 
            padding: 0; 
            background: #0b1728; 
            color: #e7eef8; 
        }}
        .wrap {{ 
            max-width: 820px; 
            margin: 0 auto; 
            padding: 28px; 
        }}
        h1 {{ 
            font-size: 24px; 
            margin: 0 0 18px; 
        }}
        .header {{ 
            display: flex; 
            align-items: center; 
            margin-bottom: 20px; 
        }}
        .header h1 {{ 
            margin: 0; 
            margin-right: 15px; 
        }}
        .logo {{ 
            height: 48px; 
            width: auto; 
            max-width: 200px;
        }}
        .card {{ 
            background: #0f2233; 
            border: 1px solid #1b3350; 
            border-radius: 12px; 
            padding: 22px; 
        }}
        h3 {{
            color: #e7eef8;
            margin: 0 0 20px 0;
            font-size: 20px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            color: #e7eef8;
        }}
        .form-control {{
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #1b3350;
            border-radius: 6px;
            background: #0b1728;
            color: #e7eef8;
            font-size: 14px;
            box-sizing: border-box;
        }}
        .form-control:focus {{
            outline: none;
            border-color: #00b4db;
            box-shadow: 0 0 0 2px rgba(0, 180, 219, 0.2);
        }}
        .form-control[readonly] {{
            background: #162332;
            color: #a8b3c7;
        }}
        .input-group {{
            display: flex;
            gap: 8px;
            align-items: stretch;
        }}
        .input-group .form-control {{
            flex: 1;
        }}
        .btn {{
            padding: 10px 16px;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: #00b4db;
            color: white;
        }}
        .btn-primary:hover {{
            background: #0083b0;
        }}
        .btn-secondary {{
            background: #1b3350;
            color: #e7eef8;
            white-space: nowrap;
        }}
        .btn-secondary:hover {{
            background: #2a4a6b;
        }}
        .form-text {{
            display: block;
            margin-top: 5px;
            font-size: 12px;
            color: #a8b3c7;
        }}
        .form-actions {{
            margin-top: 30px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="header">
            <h1>{enterprise_name} Bitrix24</h1>
            <img src="/24-admin/logo.png" alt="Bitrix24" class="logo">
        </div>
        <div class="card">
            <h3>Настройка интеграции Bitrix24</h3>
            
            <form id="bitrix24-config-form">
                <div class="form-group">
                    <label for="incoming-webhook">Входящий вебхук</label>
                    <input type="url" id="incoming-webhook" name="incoming_webhook" 
                           value="{incoming_webhook}"
                           placeholder="https://your-portal.bitrix24.ru/rest/1/your_token/" 
                           class="form-control">
                    <small class="form-text">URL вебхука от Битрикс24 для отправки данных</small>
                </div>


                <div class="form-group">
                    <label for="outgoing-webhook">Исходящий вебхук</label>
                    <div class="input-group">
                        <input type="text" id="outgoing-webhook" name="outgoing_webhook" 
                               value="https://bot.vochi.by/api/bitrix24-crm/{webhook_uuid}" 
                               class="form-control" readonly>
                        <button type="button" class="btn btn-secondary" onclick="copyToClipboard('outgoing-webhook')">
                            Копировать
                        </button>
                    </div>
                    <small class="form-text">Используйте этот URL при создании исходящего вебхука в Битрикс24</small>
                </div>

                <div class="form-group">
                    <label for="webhook-token">Токен безопасности от Битрикс24</label>
                    <input type="text" id="webhook-token" name="webhook_token" 
                           value="{webhook_token}"
                           placeholder="Вставьте сюда application_token из Битрикс24" 
                           class="form-control">
                    <small class="form-text">Токен application_token, который предоставит Битрикс24 при создании исходящего вебхука</small>
                </div>

                <div class="form-group">
                    <label>
                        <input type="checkbox" id="enabled" name="enabled" style="margin-right: 8px; width: 18px; height: 18px; accent-color: #00b4db;" {"checked" if enabled else ""}>
                        Активен?
                    </label>
                </div>

                <div class="form-actions" style="display: flex; align-items: center; gap: 16px;">
                    <button type="button" id="saveBtn" class="btn btn-primary">Сохранить и зарегистрировать</button>
                    <button type="button" id="refreshBtn" class="btn" style="background: #059669;">Обновить</button>
                    <button type="button" id="deleteBtn" class="btn" style="background: #dc2626; margin-left: auto;">Удалить интеграцию</button>
                    <span id="msg" class="form-text"></span>
                </div>
            </form>
        </div>
        
        <!-- Блок отображения пользователей Bitrix24 -->
        <div class="card" id="usersCard" style="display: none; margin-top: 20px;">
            <h3 style="margin: 0 0 20px 0;">Пользователи</h3>
            <div id="usersList"></div>
            <div id="usersLoading" style="display: none; color: #a8b3c7; font-style: italic;">Загрузка пользователей...</div>
        </div>
        
    </div>
    
    <script src="/24-admin/app.js?v=202509121100"></script>
</body>
</html>
        """
        
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        logger.error(f"Ошибка админки Битрикс24: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.post("/24-admin/{enterprise_number}/save")
async def save_bitrix24_config(enterprise_number: str, request: Request):
    """Сохранение конфигурации Битрикс24"""
    try:
        # Получаем данные из формы
        form_data = await request.form()
        incoming_webhook = form_data.get('incoming_webhook', '').strip()
        webhook_token = form_data.get('webhook_token', '').strip()
        enabled = form_data.get('enabled') == 'on'
        
        logger.info(f"💾 Сохранение конфигурации Битрикс24 для {enterprise_number}")
        
        # Подключаемся к БД
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            # Получаем текущую конфигурацию
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                raise HTTPException(status_code=404, detail="Предприятие не найдено")
            
            # Обновляем конфигурацию
            integrations_config = row['integrations_config'] or {}
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            bitrix24_config = integrations_config.get('bitrix24', {})
            
            # Генерируем UUID если его еще нет
            if not bitrix24_config.get('webhook_uuid'):
                bitrix24_config['webhook_uuid'] = str(uuid.uuid4())
                logger.info(f"🆔 Сгенерирован новый UUID для {enterprise_number}: {bitrix24_config['webhook_uuid']}")
            
            # Обновляем значения
            bitrix24_config['incoming_webhook'] = incoming_webhook
            bitrix24_config['webhook_token'] = webhook_token
            bitrix24_config['enabled'] = enabled
            bitrix24_config['updated_at'] = datetime.now().isoformat()
            
            # Сохраняем в БД
            integrations_config['bitrix24'] = bitrix24_config
            
            await conn.execute(
                "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
                json.dumps(integrations_config), enterprise_number
            )
            
            # Сбрасываем локальный кеш
            if enterprise_number in bitrix24_config_cache:
                del bitrix24_config_cache[enterprise_number]
            
            # Отправляем обновленную конфигурацию в глобальный кеш
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.put(
                        f"http://127.0.0.1:8020/config/{enterprise_number}/bitrix24",
                        json=bitrix24_config
                    )
                    if response.status_code == 200:
                        logger.info(f"🔄 Bitrix24 config sent to global cache for {enterprise_number}")
                    else:
                        logger.warning(f"⚠️ Failed to update global cache: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Error updating global cache for {enterprise_number}: {e}")
            
            logger.info(f"✅ Конфигурация Битрикс24 сохранена для {enterprise_number}")
            
            return {"success": True, "message": "Конфигурация сохранена"}
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"💥 Ошибка сохранения конфигурации Битрикс24: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения: {str(e)}")

@app.post("/24-admin/{enterprise_number}/delete")
async def delete_bitrix24_config(enterprise_number: str):
    """Удаление конфигурации Битрикс24"""
    try:
        logger.info(f"🗑️ Удаление конфигурации Битрикс24 для {enterprise_number}")
        
        # Подключаемся к БД
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            # Получаем текущую конфигурацию
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                raise HTTPException(status_code=404, detail="Предприятие не найдено")
            
            # Удаляем bitrix24 секцию
            integrations_config = row['integrations_config'] or {}
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            
            # Удаляем секцию bitrix24
            if 'bitrix24' in integrations_config:
                del integrations_config['bitrix24']
            
            # Сохраняем обновленную конфигурацию
            await conn.execute(
                "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
                json.dumps(integrations_config), enterprise_number
            )
            
            return {"success": True, "message": "Интеграция удалена"}
            
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"💥 Ошибка удаления конфигурации Битрикс24: {e}")
        return {"success": False, "error": f"Ошибка удаления: {str(e)}"}

@app.post("/24-admin/api/refresh-managers/{enterprise_number}")
async def refresh_bitrix24_managers(enterprise_number: str):
    """Загрузка менеджеров из Битрикс24"""
    try:
        logger.info(f"👥 Загрузка пользователей Битрикс24 для {enterprise_number}")
        
        # Получаем конфигурацию
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                return {"success": False, "error": "Предприятие не найдено"}
            
            integrations_config = row['integrations_config'] or {}
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            
            bitrix24_config = integrations_config.get('bitrix24', {})
            incoming_webhook = bitrix24_config.get('incoming_webhook')
            
            if not incoming_webhook:
                return {"success": False, "error": "Не настроен входящий webhook"}
            
            # Загружаем пользователей из Битрикс24
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{incoming_webhook}user.get", json={
                    "filter": {"ACTIVE": "Y"},
                    "select": ["ID", "NAME", "LAST_NAME", "EMAIL", "UF_PHONE_INNER"]
                })
                
                if response.status_code == 200:
                    data = response.json()
                    users = []
                    
                    if data.get('result'):
                        # Получаем существующие назначения
                        user_extensions = bitrix24_config.get('user_extensions', {})
                        
                        for user_data in data['result']:
                            users.append({
                                "id": user_data.get('ID'),
                                "name": f"{user_data.get('NAME', '')} {user_data.get('LAST_NAME', '')}".strip(),
                                "email": user_data.get('EMAIL', ''),
                                "current_extension": user_extensions.get(user_data.get('ID')),
                                "bitrix_extension": user_data.get('UF_PHONE_INNER')
                            })
                    
                    return {"success": True, "users": users}
                else:
                    return {"success": False, "error": f"Ошибка API Битрикс24: {response.status_code}"}
        
        finally:
            await conn.close()
    
    except Exception as e:
        logger.error(f"💥 Ошибка загрузки пользователей Битрикс24: {e}")
        return {"success": False, "error": f"Ошибка: {str(e)}"}

@app.get("/24-admin/api/internal-phones/{enterprise_number}")
async def get_internal_phones(enterprise_number: str):
    """Получение списка внутренних номеров для предприятия"""
    try:
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            # Получаем внутренние номера из таблицы user_internal_phones
            rows = await conn.fetch("""
                SELECT 
                    uip.extension,
                    u.enterprise_number,
                    u.bitrix24_user_id,
                    CASE 
                        WHEN u.bitrix24_user_id IS NOT NULL THEN u.bitrix24_user_id::text
                        ELSE NULL 
                    END as assigned_user_id
                FROM user_internal_phones uip
                LEFT JOIN users u ON u.internal_phone = uip.extension 
                    AND u.enterprise_number = $1 
                    AND u.bitrix24_user_id IS NOT NULL
                WHERE uip.enterprise_number = $1
                ORDER BY uip.extension::int
            """, enterprise_number)
            
            phones = []
            for row in rows:
                phones.append({
                    "extension": row['extension'],
                    "assigned_user_id": row['assigned_user_id']
                })
            
            return phones
        
        finally:
            await conn.close()
    
    except Exception as e:
        logger.error(f"💥 Ошибка получения внутренних номеров: {e}")
        return []

@app.post("/24-admin/api/save-extensions/{enterprise_number}")
async def save_bitrix24_extensions(enterprise_number: str, request: Request):
    """Сохранение назначений внутренних номеров пользователям Битрикс24"""
    try:
        assignments = await request.json()
        logger.info(f"📞 Сохранение назначений номеров для {enterprise_number}: {assignments}")
        
        conn = await asyncpg.connect(
            host="localhost", port=5432, user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==", database="postgres"
        )
        
        try:
            # Получаем конфигурацию Битрикс24
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                return {"success": False, "error": "Предприятие не найдено"}
            
            integrations_config = row['integrations_config'] or {}
            if isinstance(integrations_config, str):
                integrations_config = json.loads(integrations_config)
            
            bitrix24_config = integrations_config.get('bitrix24', {})
            incoming_webhook = bitrix24_config.get('incoming_webhook')
            
            if not incoming_webhook:
                return {"success": False, "error": "Не настроен входящий webhook"}
            
            # Сохраняем назначения в локальной конфигурации
            bitrix24_config['user_extensions'] = assignments
            integrations_config['bitrix24'] = bitrix24_config
            
            await conn.execute(
                "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
                json.dumps(integrations_config), enterprise_number
            )
            
            # Обновляем таблицу users для синхронизации с другими сервисами
            # Сначала очищаем старые назначения для этого предприятия
            await conn.execute(
                "UPDATE users SET internal_phone = NULL, bitrix24_user_id = NULL WHERE enterprise_number = $1 AND bitrix24_user_id IS NOT NULL",
                enterprise_number
            )
            
            # Устанавливаем новые назначения
            for user_id, extension in assignments.items():
                await conn.execute("""
                    INSERT INTO users (enterprise_number, bitrix24_user_id, internal_phone, created_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (enterprise_number, bitrix24_user_id) 
                    DO UPDATE SET internal_phone = $3, updated_at = NOW()
                """, enterprise_number, int(user_id), extension)
            
            # Обновляем UF_PHONE_INNER в Битрикс24 для каждого пользователя
            import httpx
            async with httpx.AsyncClient() as client:
                for user_id, extension in assignments.items():
                    try:
                        response = await client.post(f"{incoming_webhook}user.update", json={
                            "ID": user_id,
                            "UF_PHONE_INNER": extension
                        })
                        
                        if response.status_code != 200:
                            logger.warning(f"⚠️ Не удалось обновить UF_PHONE_INNER для пользователя {user_id}")
                    
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка обновления номера в Битрикс24 для пользователя {user_id}: {e}")
            
            return {"success": True, "message": "Назначения сохранены"}
        
        finally:
            await conn.close()
    
    except Exception as e:
        logger.error(f"💥 Ошибка сохранения назначений: {e}")
        return {"success": False, "error": f"Ошибка: {str(e)}"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8024, log_level="info")
