#!/usr/bin/env python3
"""
SMS Sending Service для предприятий
Микросервис для отправки SMS через WebSMS API с индивидуальными credentials из таблицы enterprises
Порт: 8014
"""

import json
import logging
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
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('user_sms_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Создание FastAPI приложения
app = FastAPI(
    title="User SMS Sending Service", 
    description="Микросервис отправки SMS для предприятий через WebSMS API",
    version="1.0.0"
)

# Конфигурация WebSMS API (базовые настройки)
WEBSMS_CONFIG = {
    "url": "https://cabinet.websms.by/api/send/sms",
    "balance_url": "https://cabinet.websms.by/api/balances",
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
class UserSMSRequest(BaseModel):
    enterprise_number: str = Field(..., description="Номер предприятия")
    phone: str = Field(..., description="Номер телефона получателя (с +)")
    text: str = Field(..., max_length=1000, description="Текст сообщения")
    sender: Optional[str] = Field(None, max_length=11, description="Имя отправителя (опционально)")
    custom_id: Optional[str] = Field(None, max_length=20, description="Пользовательский ID")

class UserSMSResponse(BaseModel):
    success: bool
    message_id: Optional[int] = None
    price: Optional[float] = None
    parts: Optional[int] = None
    amount: Optional[float] = None
    custom_id: Optional[str] = None
    error: Optional[str] = None

class StatusResponse(BaseModel):
    service: str
    status: str
    timestamp: datetime
    config: dict

def get_enterprise_credentials(enterprise_number: str) -> dict:
    """Получение credentials предприятия из БД"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute(
            "SELECT number, name, custom_domain FROM enterprises WHERE number = %s AND custom_domain IS NOT NULL AND custom_domain != ''",
            (enterprise_number,)
        )
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            logger.warning(f"Enterprise {enterprise_number} not found or has no custom_domain")
            return None
            
        # Парсинг custom_domain: "user@domain.com API_KEY"
        custom_domain = result['custom_domain'].strip()
        if ' ' not in custom_domain:
            logger.error(f"Invalid custom_domain format for enterprise {enterprise_number}: {custom_domain}")
            return None
            
        user, apikey = custom_domain.split(' ', 1)
        
        return {
            "user": user.strip(),
            "apikey": apikey.strip(),
            "enterprise_number": result['number'],
            "enterprise_name": result['name']
        }
        
    except Exception as e:
        logger.error(f"Error getting enterprise credentials: {str(e)}")
        return None

def save_user_sms_to_db(
    enterprise_number: str,
    enterprise_name: str,
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
    """Сохранение SMS в БД user_sms_send"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        sql = """
        INSERT INTO user_sms_send (
            enterprise_number, enterprise_name, phone, text, sender, 
            message_id, custom_id, status, price, parts, amount, 
            error_message, service_name, request_ip, user_agent, 
            response_data, sent_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            CASE WHEN %s = 'success' THEN NOW() ELSE NULL END
        )
        """
        
        cursor.execute(sql, (
            enterprise_number, enterprise_name, phone, text, sender,
            message_id, custom_id, status, price, parts, amount,
            error_message, service_name, request_ip, user_agent,
            json.dumps(response_data) if response_data else None,
            status
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"User SMS записан в БД: enterprise={enterprise_number}, phone={phone}, message_id={message_id}, status={status}")
        
    except Exception as e:
        logger.error(f"Ошибка записи User SMS в БД: {str(e)}")

async def send_user_sms_to_websms(
    enterprise_credentials: dict,
    phone: str, 
    text: str, 
    sender: str = None, 
    custom_id: str = None,
    service_name: str = None, 
    request_ip: str = None, 
    user_agent: str = None
) -> UserSMSResponse:
    """Отправка SMS через WebSMS API с credentials предприятия"""
    
    enterprise_number = enterprise_credentials['enterprise_number']
    enterprise_name = enterprise_credentials['enterprise_name']
    
    try:
        params = {
            "user": enterprise_credentials['user'],
            "apikey": enterprise_credentials['apikey'],
            "msisdn": phone,
            "text": text,
            "sender": sender or WEBSMS_CONFIG["default_sender"]
        }
        
        if custom_id:
            params["custom_id"] = custom_id
        
        logger.info(f"Отправка SMS для предприятия {enterprise_number} на {phone}: {text[:50]}...")
        
        response = requests.get(
            WEBSMS_CONFIG["url"],
            params=params,
            headers={'Accept': 'application/json'},
            timeout=WEBSMS_CONFIG["timeout"]
        )
        
        if response.status_code == 200:
            if 'application/json' in response.headers.get('Content-Type', ''):
                try:
                    result = response.json()
                    if result.get('status') == True:
                        logger.info(f"User SMS отправлено успешно: ID {result.get('message_id')} для предприятия {enterprise_number}")
                        
                        # Сохраняем успешную отправку в БД
                        save_user_sms_to_db(
                            enterprise_number=enterprise_number,
                            enterprise_name=enterprise_name,
                            phone=phone,
                            text=text,
                            sender=sender or WEBSMS_CONFIG["default_sender"],
                            message_id=result.get('message_id'),
                            custom_id=result.get('custom_id') if result.get('custom_id') else custom_id,
                            status='success',
                            price=result.get('price'),
                            parts=result.get('parts'),
                            amount=result.get('amount'),
                            service_name=service_name or 'user_api',
                            request_ip=request_ip,
                            user_agent=user_agent,
                            response_data=result
                        )
                        
                        return UserSMSResponse(
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
                        save_user_sms_to_db(
                            enterprise_number=enterprise_number,
                            enterprise_name=enterprise_name,
                            phone=phone,
                            text=text,
                            sender=sender or WEBSMS_CONFIG["default_sender"],
                            custom_id=custom_id,
                            status='failed',
                            error_message=error_msg,
                            service_name=service_name or 'user_api',
                            request_ip=request_ip,
                            user_agent=user_agent,
                            response_data=result
                        )
                        
                        return UserSMSResponse(success=False, error=error_msg)
                        
                except json.JSONDecodeError:
                    error_msg = f"Invalid JSON response: {response.text}"
                    logger.error(error_msg)
                    save_user_sms_to_db(
                        enterprise_number=enterprise_number,
                        enterprise_name=enterprise_name,
                        phone=phone, text=text, sender=sender,
                        custom_id=custom_id, status='failed',
                        error_message=error_msg, service_name=service_name,
                        request_ip=request_ip, user_agent=user_agent
                    )
                    return UserSMSResponse(success=False, error=error_msg)
            else:
                error_msg = f"Unexpected content type: {response.headers.get('Content-Type')}"
                logger.error(error_msg)
                save_user_sms_to_db(
                    enterprise_number=enterprise_number,
                    enterprise_name=enterprise_name,
                    phone=phone, text=text, sender=sender,
                    custom_id=custom_id, status='failed',
                    error_message=error_msg, service_name=service_name,
                    request_ip=request_ip, user_agent=user_agent
                )
                return UserSMSResponse(success=False, error=error_msg)
        else:
            error_msg = f"HTTP error {response.status_code}: {response.text}"
            logger.error(error_msg)
            save_user_sms_to_db(
                enterprise_number=enterprise_number,
                enterprise_name=enterprise_name,
                phone=phone, text=text, sender=sender,
                custom_id=custom_id, status='failed',
                error_message=error_msg, service_name=service_name,
                request_ip=request_ip, user_agent=user_agent
            )
            return UserSMSResponse(success=False, error=error_msg)
            
    except Exception as e:
        error_msg = f"Exception during User SMS sending: {str(e)}"
        logger.error(error_msg)
        save_user_sms_to_db(
            enterprise_number=enterprise_number,
            enterprise_name=enterprise_name,
            phone=phone, text=text, sender=sender,
            custom_id=custom_id, status='failed',
            error_message=error_msg, service_name=service_name,
            request_ip=request_ip, user_agent=user_agent
        )
        return UserSMSResponse(success=False, error=error_msg)

# API Endpoints

@app.get("/", response_model=StatusResponse)
async def root():
    """Статус сервиса"""
    return StatusResponse(
        service="User SMS Sending Service",
        status="running",
        timestamp=datetime.now(),
        config={
            "websms_url": WEBSMS_CONFIG["url"],
            "default_sender": WEBSMS_CONFIG["default_sender"],
            "port": 8014
        }
    )

@app.post("/send", response_model=UserSMSResponse)
async def send_user_sms(sms_request: UserSMSRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Отправка SMS от имени предприятия
    """
    try:
        if not sms_request.phone.startswith('+'):
            raise HTTPException(status_code=400, detail="Phone number must start with +")
        
        # Получаем credentials предприятия
        enterprise_credentials = get_enterprise_credentials(sms_request.enterprise_number)
        if not enterprise_credentials:
            raise HTTPException(
                status_code=404, 
                detail=f"Enterprise {sms_request.enterprise_number} not found or has no WebSMS configuration"
            )
        
        # Получаем информацию о запросе
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        # Отправка SMS
        result = await send_user_sms_to_websms(
            enterprise_credentials=enterprise_credentials,
            phone=sms_request.phone,
            text=sms_request.text,
            sender=sms_request.sender,
            custom_id=sms_request.custom_id,
            service_name='user_api_endpoint',
            request_ip=client_ip,
            user_agent=user_agent
        )
        
        if result.success:
            logger.info(f"User SMS sent successfully to {sms_request.phone} from enterprise {sms_request.enterprise_number}, ID: {result.message_id}")
        else:
            logger.error(f"Failed to send User SMS to {sms_request.phone} from enterprise {sms_request.enterprise_number}: {result.error}")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in send_user_sms: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        # Проверяем подключение к БД
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy" if db_status == "ok" else "unhealthy",
        "timestamp": datetime.now(),
        "database": db_status,
        "port": 8014
    }

def check_enterprise_balance_cli(enterprise_number: str):
    """Функция для проверки баланса предприятия из командной строки"""
    import asyncio
    
    async def get_enterprise_balance_and_print():
        # Получаем credentials предприятия
        enterprise_credentials = get_enterprise_credentials(enterprise_number)
        if not enterprise_credentials:
            print(f"❌ Предприятие {enterprise_number} не найдено или не имеет WebSMS настроек")
            print(f"   Проверьте поле custom_domain в таблице enterprises")
            return
            
        try:
            # Делаем запрос к WebSMS API
            params = {
                "user": enterprise_credentials['user'],
                "apikey": enterprise_credentials['apikey']
            }
            
            logger.info(f"Запрос баланса для предприятия {enterprise_number}...")
            response = requests.get(
                "https://cabinet.websms.by/api/balances",
                params=params,
                headers={'Accept': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                if 'application/json' in response.headers.get('Content-Type', ''):
                    try:
                        result = response.json()
                        print("💰 БАЛАНС WEBSMS:")
                        print("="*60)
                        print(f"🏢 Предприятие: {enterprise_number} ({enterprise_credentials['enterprise_name']})")
                        print(f"👤 Пользователь: {enterprise_credentials['user']}")
                        print("-"*60)
                        
                        if isinstance(result, dict) and 'sms' in result:
                            # Формат WebSMS: {"status": True, "sms": 39.056508, "viber": 0}
                            sms_balance = result.get('sms', 'N/A')
                            viber_balance = result.get('viber', 'N/A')
                            print(f"   📱 SMS: {sms_balance} BYN")
                            print(f"   💬 Viber: {viber_balance} BYN")
                            print(f"   💰 Общий доступный баланс: {sms_balance} BYN")
                        elif isinstance(result, list):
                            # Альтернативный формат (массив счетов)
                            for account in result:
                                currency = account.get('currency', 'N/A')
                                amount = account.get('amount', 'N/A')
                                print(f"   💵 {currency}: {amount}")
                        else:
                            print(f"   💰 Полный ответ: {result}")
                        print("="*60)
                        
                    except json.JSONDecodeError:
                        print(f"❌ Ошибка: Некорректный JSON ответ от WebSMS API")
                        print(f"   Ответ: {response.text}")
                else:
                    print(f"❌ Ошибка: Неожиданный тип ответа от WebSMS API")
                    print(f"   Content-Type: {response.headers.get('Content-Type')}")
            else:
                print(f"❌ Ошибка HTTP {response.status_code}: {response.text}")
                
        except Exception as e:
            print(f"❌ Ошибка при запросе баланса: {str(e)}")
            
    asyncio.run(get_enterprise_balance_and_print())

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "balance":
        if len(sys.argv) > 2:
            enterprise_number = sys.argv[2]
            check_enterprise_balance_cli(enterprise_number)
        else:
            print("❌ Ошибка: Укажите номер предприятия")
            print("   Использование: python3 send_user_sms.py balance 0367")
    else:
        logger.info("Starting User SMS Sending Service on port 8014...")
        uvicorn.run(
            "send_user_sms:app",
            host="0.0.0.0",
            port=8014,
            reload=False,
            log_level="info"
        ) 