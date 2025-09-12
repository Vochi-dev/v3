#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Битрикс24 интеграция - Webhook сервис
Порт: 8024
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any

import asyncpg
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
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
                "SELECT name FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                raise HTTPException(status_code=404, detail="Предприятие не найдено")
            
            enterprise_name = row['name'] or f"Предприятие {enterprise_number}"
            
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
    </style>
</head>
<body>
    <div class="wrap">
        <div class="header">
            <h1>{enterprise_name} Bitrix24</h1>
            <img src="/24-admin/logo.png" alt="Bitrix24" class="logo">
        </div>
        <div class="card">
            <p>Админка Битрикс24 находится в разработке...</p>
        </div>
    </div>
</body>
</html>
        """
        
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        logger.error(f"Ошибка админки Битрикс24: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8024, log_level="info")
