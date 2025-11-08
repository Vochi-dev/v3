#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Auth Service
Микросервис для авторизации пользователей через Telegram-ботов предприятий
Порт: 8016
"""

import asyncio
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import secrets

import asyncpg
import uvicorn
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import httpx

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/telegram_auth_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Telegram Auth Service",
    description="Сервис авторизации пользователей через Telegram-ботов предприятий",
    version="1.0.0"
)

# Конфигурация БД PostgreSQL
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg==",
    "database": "postgres"
}

# URL сервисов
EMAIL_SERVICE_URL = "http://localhost:8015"  # auth.py для email
SMS_SERVICE_URL = "http://localhost:8013"    # send_service_sms.py

# ═══════════════════════════════════════════════════════════════════════════════
# МОДЕЛИ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramAuthRequest(BaseModel):
    """Запрос на авторизацию через телеграм"""
    email: str = Field(..., description="Email пользователя")
    enterprise_number: str = Field(..., description="Номер предприятия")
    telegram_id: int = Field(..., description="Telegram ID пользователя")

class TelegramCodeVerifyRequest(BaseModel):
    """Запрос на проверку кода"""
    email: str = Field(..., description="Email пользователя")
    code: str = Field(..., description="6-значный код")
    telegram_id: int = Field(..., description="Telegram ID пользователя")

class TelegramAuthResponse(BaseModel):
    """Ответ сервиса авторизации"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

# ═══════════════════════════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ К БД
# ═══════════════════════════════════════════════════════════════════════════════

async def get_db_connection():
    """Получение подключения к БД"""
    try:
        return await asyncpg.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise HTTPException(status_code=500, detail="Database connection error")

# ═══════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

def generate_auth_code() -> str:
    """Генерация 6-значного кода"""
    return ''.join(random.choices(string.digits, k=6))

async def get_bot_username(bot_token: str) -> str:
    """Получить username бота через Telegram API"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.telegram.org/bot{bot_token}/getMe",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return data["result"]["username"]
    except Exception as e:
        logger.error(f"Ошибка получения username бота: {e}")
    
    return "unknown_bot"  # fallback

async def get_user_by_email_and_enterprise(email: str, enterprise_number: str) -> Optional[Dict]:
    """Получение пользователя по email и номеру предприятия"""
    conn = await get_db_connection()
    try:
        query = """
        SELECT u.id, u.email, u.first_name, u.last_name, u.personal_phone, 
               u.enterprise_number, u.telegram_authorized, u.telegram_tg_id,
               u.telegram_auth_blocked,
               e.name as enterprise_name, e.bot_token, e.chat_id
        FROM users u
        JOIN enterprises e ON u.enterprise_number = e.number
        WHERE u.email = $1 AND u.enterprise_number = $2
        """
        result = await conn.fetchrow(query, email, enterprise_number)
        return dict(result) if result else None
    finally:
        await conn.close()

async def update_telegram_auth_code(user_id: int, code: str, telegram_id: int) -> bool:
    """Обновление кода авторизации для пользователя"""
    conn = await get_db_connection()
    try:
        expire_time = datetime.utcnow() + timedelta(minutes=10)  # Код действует 10 минут
        
        # Обновляем поля telegram_auth_code и telegram_auth_expires
        query = """
        UPDATE users 
        SET telegram_auth_code = $1, 
            telegram_auth_expires = $2,
            telegram_tg_id = $3
        WHERE id = $4
        """
        await conn.execute(query, code, expire_time, telegram_id, user_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления кода авторизации: {e}")
        return False
    finally:
        await conn.close()

async def verify_and_authorize_user(email: str, code: str, telegram_id: int) -> Dict[str, Any]:
    """Проверка кода и авторизация пользователя"""
    conn = await get_db_connection()
    try:
        # Получаем пользователя с кодом
        query = """
        SELECT u.id, u.email, u.first_name, u.last_name, 
               u.telegram_auth_code, u.telegram_auth_expires, u.telegram_tg_id,
               e.bot_token, e.number as enterprise_number
        FROM users u
        JOIN enterprises e ON u.enterprise_number = e.number
        WHERE u.email = $1 AND u.telegram_tg_id = $2
        """
        user = await conn.fetchrow(query, email, telegram_id)
        
        if not user:
            return {"success": False, "message": "Пользователь не найден"}
            
        # Проверяем код
        if user['telegram_auth_code'] != code:
            return {"success": False, "message": "Неверный код"}
            
        # Проверяем срок действия
        if datetime.utcnow() > user['telegram_auth_expires']:
            return {"success": False, "message": "Код истек. Запросите новый"}
            
        # Авторизуем пользователя
        await conn.execute("""
            UPDATE users 
            SET telegram_authorized = TRUE,
                telegram_auth_code = NULL,
                telegram_auth_expires = NULL
            WHERE id = $1
        """, user['id'])
        
        # Записываем в telegram_users для совместимости
        await conn.execute("""
            INSERT INTO telegram_users (tg_id, email, bot_token)
            VALUES ($1, $2, $3)
            ON CONFLICT (tg_id, bot_token) DO UPDATE SET
                email = EXCLUDED.email
        """, telegram_id, email, user['bot_token'])
        
        return {
            "success": True, 
            "message": "Авторизация успешна",
            "data": {
                "user_id": user['id'],
                "full_name": f"{user['first_name']} {user['last_name']}",
                "enterprise_number": user['enterprise_number']
            }
        }
        
    except Exception as e:
        logger.error(f"Ошибка авторизации пользователя: {e}")
        return {"success": False, "message": "Ошибка сервера"}
    finally:
        await conn.close()

async def send_auth_code_email(email: str, code: str, enterprise_name: str, bot_username: str = "") -> bool:
    """Отправка кода авторизации на email"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EMAIL_SERVICE_URL}/send-telegram-auth-code",
                data={  # Используем data вместо json для Form данных
                    "email": email,
                    "code": code,
                    "enterprise_name": enterprise_name,
                    "bot_username": bot_username
                },
                timeout=10
            )
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")
        return False

async def send_auth_code_sms(phone: str, code: str, enterprise_name: str, bot_username: str = "") -> bool:
    """Отправка кода авторизации на SMS"""
    if not phone:
        return True  # Если телефона нет - не отправляем, но не считаем ошибкой
    
    # Формируем текст SMS БЕЗ ссылки на бота (по требованию пользователя)
    sms_text = f"Код авторизации Telegram-бота {enterprise_name}: {code}. Код действует 10 минут."
    
    logger.info(f"Sending SMS to {phone}: {sms_text}")
        
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SMS_SERVICE_URL}/send",
                json={
                    "phone": phone,
                    "text": sms_text,
                    "sender": "Vochi-CRM"
                },
                timeout=10
            )
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Ошибка отправки SMS: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/status")
async def get_status():
    """Проверка состояния сервиса"""
    return {
        "service": "Telegram Auth Service",
        "status": "running",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.post("/start_auth_flow", response_model=TelegramAuthResponse)
async def start_telegram_auth_flow(request: TelegramAuthRequest):
    """
    Начало процесса авторизации через Telegram
    
    1. Проверяем существование пользователя
    2. Генерируем 6-значный код
    3. Отправляем код на email и SMS (если есть)
    """
    try:
        # Проверяем пользователя
        user = await get_user_by_email_and_enterprise(request.email, request.enterprise_number)
        if not user:
            return TelegramAuthResponse(
                success=False,
                message="Пользователь с таким email не найден в данном предприятии"
            )
        
        # Проверяем, не заблокирована ли Telegram-авторизация
        if user.get('telegram_auth_blocked'):
            # Отправляем уведомление о блокировке
            blocked_notification = f"""🚫 Ваш аккаунт заблокирован для использования Telegram-бота предприятия "{user['enterprise_name']}".

Обратитесь к администратору предприятия для разблокировки."""
            
            try:
                from telegram import Bot
                from telegram.error import TelegramError
                
                bot = Bot(token=user['bot_token'])
                await bot.send_message(chat_id=request.telegram_id, text=blocked_notification)
                logger.info(f"📱 Уведомление о блокировке отправлено пользователю {request.telegram_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления о блокировке: {e}")
            
            return TelegramAuthResponse(
                success=False,
                message="Telegram-авторизация заблокирована администратором"
            )
        
        # Проверяем, не авторизован ли уже
        if user.get('telegram_authorized'):
            return TelegramAuthResponse(
                success=False,
                message="Вы уже авторизованы в Telegram-боте"
            )
        
        # Генерируем код
        code = generate_auth_code()
        
        # Сохраняем код в БД
        code_saved = await update_telegram_auth_code(user['id'], code, request.telegram_id)
        if not code_saved:
            return TelegramAuthResponse(
                success=False,
                message="Ошибка сохранения кода авторизации"
            )
        
        # Получаем username бота для ссылки
        bot_username = await get_bot_username(user['bot_token']) if user.get('bot_token') else ""
        
        # Отправляем код на email и SMS параллельно
        email_task = send_auth_code_email(request.email, code, user['enterprise_name'], bot_username)
        sms_task = send_auth_code_sms(user.get('personal_phone'), code, user['enterprise_name'], bot_username)
        
        email_sent, sms_sent = await asyncio.gather(email_task, sms_task)
        
        # Формируем ответ
        messages = []
        if email_sent:
            messages.append("📧 Код отправлен на email")
        if sms_sent and user.get('personal_phone'):
            messages.append("📱 Код отправлен на SMS")
        
        if not email_sent and not sms_sent:
            return TelegramAuthResponse(
                success=False,
                message="Ошибка отправки кода. Попробуйте позже"
            )
        
        return TelegramAuthResponse(
            success=True,
            message=f"Код авторизации отправлен! {' и '.join(messages)}. Действует 10 минут.",
            data={
                "email_sent": email_sent,
                "sms_sent": sms_sent,
                "user_name": f"{user['first_name']} {user['last_name']}"
            }
        )
        
    except Exception as e:
        logger.error(f"Ошибка в start_telegram_auth_flow: {e}")
        return TelegramAuthResponse(
            success=False,
            message="Внутренняя ошибка сервера"
        )

@app.post("/verify_code", response_model=TelegramAuthResponse)
async def verify_telegram_code(request: TelegramCodeVerifyRequest):
    """
    Проверка 6-значного кода и авторизация пользователя
    """
    try:
        result = await verify_and_authorize_user(request.email, request.code, request.telegram_id)
        
        return TelegramAuthResponse(
            success=result["success"],
            message=result["message"],
            data=result.get("data")
        )
        
    except Exception as e:
        logger.error(f"Ошибка в verify_telegram_code: {e}")
        return TelegramAuthResponse(
            success=False,
            message="Внутренняя ошибка сервера"
        )

@app.get("/check_auth_status/{telegram_id}")
async def check_telegram_auth_status(telegram_id: int):
    """Проверка статуса авторизации пользователя по Telegram ID"""
    conn = await get_db_connection()
    try:
        query = """
        SELECT u.id, u.email, u.first_name, u.last_name, 
               u.telegram_authorized, u.enterprise_number,
               e.name as enterprise_name
        FROM users u
        JOIN enterprises e ON u.enterprise_number = e.number
        WHERE u.telegram_tg_id = $1 AND u.telegram_authorized = TRUE
        """
        user = await conn.fetchrow(query, telegram_id)
        
        if user:
            return {
                "authorized": True,
                "user": {
                    "id": user['id'],
                    "email": user['email'],
                    "full_name": f"{user['first_name']} {user['last_name']}",
                    "enterprise_number": user['enterprise_number'],
                    "enterprise_name": user['enterprise_name']
                }
            }
        else:
            return {"authorized": False}
            
    except Exception as e:
        logger.error(f"Ошибка проверки статуса авторизации: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        await conn.close()

@app.post("/revoke_auth/{user_id}")
async def revoke_telegram_auth(user_id: int):
    """Отзыв авторизации пользователя (для админов)"""
    conn = await get_db_connection()
    try:
        # Сбрасываем авторизацию
        await conn.execute("""
            UPDATE users 
            SET telegram_authorized = FALSE,
                telegram_tg_id = NULL,
                telegram_auth_code = NULL,
                telegram_auth_expires = NULL
            WHERE id = $1
        """, user_id)
        
        # Удаляем из telegram_users
        await conn.execute("""
            DELETE FROM telegram_users 
            WHERE tg_id = (SELECT telegram_tg_id FROM users WHERE id = $1)
        """, user_id)
        
        return {"success": True, "message": "Авторизация отозвана"}
        
    except Exception as e:
        logger.error(f"Ошибка отзыва авторизации: {e}")
        return {"success": False, "message": "Ошибка сервера"}
    finally:
        await conn.close()

# ═══════════════════════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ БД (добавление необходимых полей если их нет)
# ═══════════════════════════════════════════════════════════════════════════════

async def init_database():
    """Инициализация дополнительных полей в таблице users"""
    conn = await get_db_connection()
    try:
        # Добавляем поля для telegram авторизации если их нет
        await conn.execute("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS telegram_auth_code VARCHAR(6),
            ADD COLUMN IF NOT EXISTS telegram_auth_expires TIMESTAMP,
            ADD COLUMN IF NOT EXISTS telegram_authorized BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS telegram_tg_id BIGINT
        """)
        
        logger.info("Поля для Telegram авторизации успешно добавлены в таблицу users")
        
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    finally:
        await conn.close()

# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК СЕРВИСА
# ═══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске сервиса"""
    logger.info("Starting Telegram Auth Service...")
    await init_database()
    logger.info("Telegram Auth Service started successfully on port 8016")

@app.post("/get_enterprise_by_token")
async def get_enterprise_by_token(request: dict):
    """Получить номер предприятия по токену бота"""
    try:
        bot_token = request.get("bot_token")
        if not bot_token:
            raise HTTPException(status_code=400, detail="Bot token обязателен")
        
        # Подключение к базе данных
        conn = await asyncpg.connect(**DB_CONFIG)
        try:
            # Поиск предприятия по bot_token
            result = await conn.fetchrow(
                "SELECT number FROM enterprises WHERE bot_token = $1",
                bot_token
            )
            
            if result:
                return {
                    "success": True,
                    "enterprise_number": result["number"]
                }
            else:
                return {
                    "success": False,
                    "enterprise_number": None,
                    "message": "Предприятие с таким bot_token не найдено"
                }
                
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Ошибка получения enterprise_number: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка сервера: {str(e)}")

if __name__ == "__main__":
    logger.info("Starting Telegram Auth Service on port 8016...")
    uvicorn.run(
        "telegram_auth_service:app",
        host="0.0.0.0",
        port=8016,
        reload=False
    )