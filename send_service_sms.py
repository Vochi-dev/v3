#!/usr/bin/env python3
"""
SMS Sending Service
Микросервис для отправки сервисных SMS через WebSMS API
Порт: 8013
"""

import asyncio
import logging
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field
import requests
from typing import Optional
import uvicorn
import psycopg2
from psycopg2.extras import RealDictCursor

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('sms_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="SMS Sending Service",
    description="Сервис для отправки сервисных SMS через WebSMS API",
    version="1.0.0"
)

# Конфигурация WebSMS API
WEBSMS_CONFIG = {
    "url": "https://cabinet.websms.by/api/send/sms",
    "user": "info@ead.by",
    "apikey": "bOeR6LslKf",
    "default_sender": "Vochi-CRM",
    "timeout": 30
}

# Конфигурация БД PostgreSQL
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "postgres", 
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg=="
}

# Pydantic модели
class SMSRequest(BaseModel):
    """Запрос на отправку SMS"""
    phone: str = Field(..., description="Номер телефона получателя в формате +375XXXXXXXXX")
    text: str = Field(..., min_length=1, max_length=1000, description="Текст сообщения")
    sender: Optional[str] = Field(None, description="Имя отправителя (по умолчанию Vochi-CRM)")
    custom_id: Optional[str] = Field(None, description="Уникальный ID сообщения")

class SMSResponse(BaseModel):
    """Ответ на отправку SMS"""
    success: bool
    message_id: Optional[int] = None
    price: Optional[float] = None
    parts: Optional[int] = None
    amount: Optional[float] = None
    custom_id: Optional[str] = None
    error: Optional[str] = None

class SMSStatusResponse(BaseModel):
    """Статус сервиса"""
    service: str = "SMS Sending Service"
    status: str = "running"
    timestamp: datetime
    config: dict

def save_sms_to_db(
    phone: str, 
    text: str, 
    sender: str, 
    message_id: int = None,
    custom_id: str = None,
    status: str = 'success',
    price: float = None,
    parts: int = None,
    amount: float = None,
    error_message: str = None,
    service_name: str = None,
    request_ip: str = None,
    user_agent: str = None,
    response_data: dict = None
):
    """Сохранение SMS в БД"""
    try:
        # Подключение к БД
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # SQL запрос для вставки
        sql = """
        INSERT INTO service_sms_send (
            phone, text, sender, message_id, custom_id, status, 
            price, parts, amount, error_message, service_name,
            request_ip, user_agent, response_data, sent_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
            CASE WHEN %s = 'success' THEN NOW() ELSE NULL END
        )
        """
        
        # Выполнение запроса
        cursor.execute(sql, (
            phone, text, sender, message_id, custom_id, status,
            price, parts, amount, error_message, service_name,
            request_ip, user_agent, 
            json.dumps(response_data) if response_data else None,
            status
        ))
        
        # Сохранение изменений
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"SMS записан в БД: phone={phone}, message_id={message_id}, status={status}")
        
    except Exception as e:
        logger.error(f"Ошибка записи SMS в БД: {str(e)}")

async def get_balance_from_websms() -> dict:
    """
    Получение баланса через WebSMS API
    """
    try:
        # Параметры для запроса баланса
        params = {
            "user": WEBSMS_CONFIG["user"],
            "apikey": WEBSMS_CONFIG["apikey"]
        }
        
        logger.info("Запрос баланса WebSMS...")
        
        # Выполнение запроса к /api/balances
        response = requests.get(
            "https://cabinet.websms.by/api/balances",
            params=params,
            headers={'Accept': 'application/json'},
            timeout=WEBSMS_CONFIG["timeout"]
        )
        
        if response.status_code == 200:
            if 'application/json' in response.headers.get('Content-Type', ''):
                try:
                    result = response.json()
                    logger.info("Баланс получен успешно")
                    return {"success": True, "balance": result}
                except json.JSONDecodeError:
                    error_msg = f"Invalid JSON response: {response.text}"
                    logger.error(error_msg)
                    return {"success": False, "error": error_msg}
            else:
                error_msg = f"Unexpected content type: {response.headers.get('Content-Type')}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
        else:
            error_msg = f"HTTP error {response.status_code}: {response.text}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        error_msg = f"Exception during balance request: {str(e)}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

async def send_sms_to_websms(phone: str, text: str, sender: str = None, custom_id: str = None, service_name: str = None, request_ip: str = None, user_agent: str = None) -> SMSResponse:
    """
    Отправка SMS через WebSMS API
    """
    try:
        # Подготовка параметров
        params = {
            "user": WEBSMS_CONFIG["user"],
            "apikey": WEBSMS_CONFIG["apikey"],
            "msisdn": phone,
            "text": text,
            "sender": sender or WEBSMS_CONFIG["default_sender"]
        }
        
        if custom_id:
            params["custom_id"] = custom_id
        
        logger.info(f"Отправка SMS на {phone}: {text[:50]}...")
        
        # Выполнение запроса
        response = requests.get(
            WEBSMS_CONFIG["url"],
            params=params,
            headers={'Accept': 'application/json'},
            timeout=WEBSMS_CONFIG["timeout"]
        )
        
        # Обработка ответа
        if response.status_code == 200:
            if 'application/json' in response.headers.get('Content-Type', ''):
                try:
                    result = response.json()
                    
                    if result.get('status') == True:
                        logger.info(f"SMS отправлено успешно: ID {result.get('message_id')}")
                        
                        # Сохраняем успешную отправку в БД
                        save_sms_to_db(
                            phone=phone,
                            text=text,
                            sender=sender or WEBSMS_CONFIG["default_sender"],
                            message_id=result.get('message_id'),
                            custom_id=result.get('custom_id') if result.get('custom_id') else custom_id,
                            status='success',
                            price=result.get('price'),
                            parts=result.get('parts'),
                            amount=result.get('amount'),
                            service_name=service_name or 'direct_api',
                            request_ip=request_ip,
                            user_agent=user_agent,
                            response_data=result
                        )
                        
                        return SMSResponse(
                            success=True,
                            message_id=result.get('message_id'),
                            price=result.get('price'),
                            parts=result.get('parts'),
                            amount=result.get('amount'),
                            custom_id=result.get('custom_id') if result.get('custom_id') else custom_id
                        )
                    else:
                        error_msg = f"WebSMS API error: {result.get('error', 'Unknown error')}"
                        logger.error(error_msg)
                        
                        # Сохраняем неудачную отправку в БД
                        save_sms_to_db(
                            phone=phone,
                            text=text,
                            sender=sender or WEBSMS_CONFIG["default_sender"],
                            custom_id=custom_id,
                            status='failed',
                            error_message=error_msg,
                            service_name=service_name or 'direct_api',
                            request_ip=request_ip,
                            user_agent=user_agent,
                            response_data=result
                        )
                        
                        return SMSResponse(success=False, error=error_msg)
                        
                except json.JSONDecodeError:
                    error_msg = f"Invalid JSON response: {response.text}"
                    logger.error(error_msg)
                    return SMSResponse(success=False, error=error_msg)
            else:
                error_msg = f"Unexpected content type: {response.headers.get('Content-Type')}"
                logger.error(error_msg)
                return SMSResponse(success=False, error=error_msg)
        else:
            error_msg = f"HTTP error {response.status_code}: {response.text}"
            logger.error(error_msg)
            return SMSResponse(success=False, error=error_msg)
            
    except Exception as e:
        error_msg = f"Exception during SMS sending: {str(e)}"
        logger.error(error_msg)
        return SMSResponse(success=False, error=error_msg)

@app.get("/", response_model=SMSStatusResponse)
async def get_status():
    """Получение статуса сервиса"""
    return SMSStatusResponse(
        timestamp=datetime.now(),
        config={
            "websms_url": WEBSMS_CONFIG["url"],
            "default_sender": WEBSMS_CONFIG["default_sender"],
            "user": WEBSMS_CONFIG["user"]
        }
    )

@app.post("/send", response_model=SMSResponse)
async def send_sms(sms_request: SMSRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Отправка SMS сообщения
    """
    try:
        # Валидация номера телефона
        if not sms_request.phone.startswith('+'):
            raise HTTPException(status_code=400, detail="Phone number must start with +")
        
        # Получаем информацию о запросе
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        # Отправка SMS
        result = await send_sms_to_websms(
            phone=sms_request.phone,
            text=sms_request.text,
            sender=sms_request.sender,
            custom_id=sms_request.custom_id,
            service_name='api_endpoint',
            request_ip=client_ip,
            user_agent=user_agent
        )
        
        # Логирование результата
        if result.success:
            logger.info(f"SMS sent successfully to {sms_request.phone}, ID: {result.message_id}")
        else:
            logger.error(f"Failed to send SMS to {sms_request.phone}: {result.error}")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in send_sms: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/send/alert")
async def send_alert_sms(phone: str, message: str, sender: str = None, request: Request = None):
    """
    Быстрая отправка алерта
    """
    sms_request = SMSRequest(
        phone=phone,
        text=f"🚨 ALERT: {message}",
        sender=sender,
        custom_id=f"al{datetime.now().strftime('%m%d%H%M%S')}"
    )
    return await send_sms(sms_request, request, BackgroundTasks())

@app.post("/send/onboarding")
async def send_onboarding_sms(phone: str, username: str, sender: str = None, request: Request = None):
    """
    Отправка приветственного SMS при регистрации
    """
    text = f"Добро пожаловать, {username}! Ваш аккаунт успешно создан. Поддержка: info@ead.by"
    
    sms_request = SMSRequest(
        phone=phone,
        text=text,
        sender=sender,
        custom_id=f"ob{datetime.now().strftime('%m%d%H%M%S')}"
    )
    return await send_sms(sms_request, request, BackgroundTasks())

@app.get("/balance")
async def get_balance():
    """Получение баланса WebSMS"""
    result = await get_balance_from_websms()
    if result["success"]:
        return {
            "success": True,
            "timestamp": datetime.now(),
            "balance": result["balance"]
        }
    else:
        raise HTTPException(status_code=500, detail=result["error"])

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        # Проверяем доступность WebSMS API
        response = requests.get(
            "https://cabinet.websms.by/api/balances",
            params={
                "user": WEBSMS_CONFIG["user"],
                "apikey": WEBSMS_CONFIG["apikey"]
            },
            timeout=5
        )
        
        websms_status = "ok" if response.status_code == 200 else "error"
        
        return {
            "status": "healthy",
            "timestamp": datetime.now(),
            "websms_api": websms_status
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": datetime.now(),
            "error": str(e)
        }

def check_balance_cli():
    """Функция для проверки баланса из командной строки"""
    import asyncio
    
    async def get_balance():
        result = await get_balance_from_websms()
        if result["success"]:
            balance = result["balance"]
            print("💰 БАЛАНС WEBSMS:")
            print("="*50)
            if isinstance(balance, list):
                for account in balance:
                    currency = account.get('currency', 'N/A')
                    amount = account.get('amount', 'N/A')
                    print(f"   {currency}: {amount}")
            else:
                print(f"   Баланс: {balance}")
            print("="*50)
        else:
            print(f"❌ Ошибка получения баланса: {result['error']}")
    
    asyncio.run(get_balance())

if __name__ == "__main__":
    import sys
    
    # Проверяем аргументы командной строки
    if len(sys.argv) > 1 and sys.argv[1] == "balance":
        check_balance_cli()
    else:
        logger.info("Starting SMS Sending Service on port 8013...")
        uvicorn.run(
            "send_service_sms:app",
            host="0.0.0.0",
            port=8013,
            reload=False,
            log_level="info"
        ) 