#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import random
import string
import logging
from datetime import datetime, timedelta
from typing import Optional

import asyncpg
import uvicorn
from fastapi import FastAPI, Request, Form, HTTPException, status, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

# Импорт для email
import smtplib
from email.message import EmailMessage
from pathlib import Path
import sys
import os
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Добавляем путь к app для импорта
sys.path.append(str(Path(__file__).parent))

# Настройки email из .env файла
EMAIL_HOST = os.getenv("EMAIL_HOST", "mailbe04.hoster.by")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "bot@vochi.by")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "G#2$9fpBcL")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_FROM = os.getenv("EMAIL_FROM") or EMAIL_HOST_USER

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

# Database configuration
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg==",
    "database": "postgres"
}

# Services configuration
SEND_SMS_SERVICE_URL = "http://localhost:8013"
DESK_SERVICE_URL = "http://localhost:8011"

# Auth configuration
CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 10
SESSION_EXPIRY_HOURS = 24

# ══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/var/log/auth_service.log', encoding='utf-8')
    ]
)

logger = logging.getLogger("auth_service")

# ══════════════════════════════════════════════════════════════════════════════
# FASTAPI ПРИЛОЖЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="User Authentication Service", version="1.0.0")

# Подключение статических файлов и шаблонов
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates/auth")

# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC МОДЕЛИ
# ══════════════════════════════════════════════════════════════════════════════

class SendCodeRequest(BaseModel):
    email: str

class VerifyCodeRequest(BaseModel):
    email: str
    code: str

# ══════════════════════════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ К БД
# ══════════════════════════════════════════════════════════════════════════════

async def get_db_connection():
    """Получение подключения к PostgreSQL"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════════════════════════

def generate_code() -> str:
    """Генерация 6-значного кода"""
    return ''.join(random.choices(string.digits, k=CODE_LENGTH))

def generate_session_token() -> str:
    """Генерация токена сессии"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=64))

async def send_email_code(email: str, code: str) -> bool:
    """Отправка кода на email"""
    try:
        msg = EmailMessage()
        msg['Subject'] = "Код авторизации Vochi CRM"
        msg['From'] = EMAIL_FROM
        msg['To'] = email
        msg.set_content(f"""Здравствуйте!

Ваш код авторизации в системе Vochi CRM: {code}

Код действителен в течение 10 минут.

Если вы не запрашивали авторизацию, просто проигнорируйте это письмо.

---
С уважением,
Команда Vochi CRM
""")

        # Отправляем email синхронно в отдельном потоке
        def send_sync():
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
                if EMAIL_USE_TLS:
                    server.starttls()
                server.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
                server.send_message(msg)
        
        await asyncio.get_event_loop().run_in_executor(None, send_sync)
        
        logger.info(f"📧 Email код отправлен на {email}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")
        return False

async def send_sms_code(phone: str, code: str) -> bool:
    """Отправка кода на SMS через send_service_sms"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SEND_SMS_SERVICE_URL}/send", 
                json={
                    "phone": phone,
                    "text": f"Ваш код авторизации: {code}",
                    "service_name": "auth_service"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success") == True:
                    logger.info(f"📱 SMS код отправлен на {phone}")
                    return True
                else:
                    logger.error(f"Ошибка SMS API: {data.get('error', data.get('message', 'Unknown error'))}")
                    return False
            else:
                logger.error(f"Ошибка SMS сервиса: {response.status_code}")
                return False
                
    except Exception as e:
        logger.error(f"Ошибка отправки SMS: {e}")
        return False

async def cleanup_expired_codes():
    """Очистка истекших кодов"""
    conn = await get_db_connection()
    if not conn:
        return
        
    try:
        deleted = await conn.execute(
            "DELETE FROM auth_codes WHERE expires_at < NOW()"
        )
        if deleted:
            logger.info(f"Удалено {deleted.replace('DELETE ', '')} истекших кодов")
    except Exception as e:
        logger.error(f"Ошибка очистки кодов: {e}")
    finally:
        await conn.close()

async def cleanup_expired_sessions():
    """Очистка истекших сессий"""
    conn = await get_db_connection()
    if not conn:
        return
        
    try:
        deleted = await conn.execute(
            "DELETE FROM user_sessions WHERE expires_at < NOW()"
        )
        if deleted:
            logger.info(f"Удалено {deleted.replace('DELETE ', '')} истекших сессий")
    except Exception as e:
        logger.error(f"Ошибка очистки сессий: {e}")
    finally:
        await conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    """Стартовая страница - ввод email"""
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/send-code", response_class=JSONResponse)
async def send_code(request: Request, email: str = Form(...)):
    """Отправка кода авторизации на email и SMS"""
    logger.info(f"🔑 Запрос кода для email: {email}")
    
    # Проверяем существование пользователя
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Ошибка подключения к БД")
    
    try:
        user = await conn.fetchrow(
            "SELECT id, enterprise_number, personal_phone FROM users WHERE email = $1 AND status = 'active'",
            email
        )
        
        if not user:
            logger.warning(f"❌ Пользователь с email {email} не найден")
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        
        # Генерируем код
        code = generate_code()
        expires_at = datetime.now() + timedelta(minutes=CODE_EXPIRY_MINUTES)
        
        # Сохраняем код в БД
        await conn.execute(
            """INSERT INTO auth_codes (email, code, expires_at) 
               VALUES ($1, $2, $3)
               ON CONFLICT (email) DO UPDATE SET 
               code = $2, created_at = NOW(), expires_at = $3""",
            email, code, expires_at
        )
        
        # Отправляем email
        email_sent = await send_email_code(email, code)
        
        # Отправляем SMS если есть телефон
        sms_sent = False
        if user['personal_phone']:
            sms_sent = await send_sms_code(user['personal_phone'], code)
        
        logger.info(f"✅ Код отправлен для {email}. Email: {email_sent}, SMS: {sms_sent}")
        
        return JSONResponse({
            "success": True,
            "message": "Код отправлен",
            "email_sent": email_sent,
            "sms_sent": sms_sent,
            "has_phone": bool(user['personal_phone'])
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка отправки кода: {e}")
        raise HTTPException(status_code=500, detail="Ошибка отправки кода")
    finally:
        await conn.close()

@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, email: str = ""):
    """Страница ввода кода"""
    return templates.TemplateResponse("verify.html", {"request": request, "email": email})

@app.post("/verify-code", response_class=JSONResponse)
async def verify_code(request: Request, email: str = Form(...), code: str = Form(...)):
    """Проверка кода и создание сессии"""
    logger.info(f"🔍 Проверка кода для email: {email}")
    
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Ошибка подключения к БД")
    
    try:
        # Проверяем код
        auth_record = await conn.fetchrow(
            "SELECT * FROM auth_codes WHERE email = $1 AND code = $2 AND expires_at > NOW()",
            email, code
        )
        
        if not auth_record:
            logger.warning(f"❌ Неверный или истекший код для {email}")
            raise HTTPException(status_code=400, detail="Неверный или истекший код")
        
        # Получаем пользователя
        user = await conn.fetchrow(
            "SELECT id, enterprise_number FROM users WHERE email = $1",
            email
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        
        # Получаем данные предприятия для формирования URL
        enterprise = await conn.fetchrow(
            "SELECT name FROM enterprises WHERE number = $1",
            user['enterprise_number']
        )
        
        enterprise_name = enterprise['name'] if enterprise else "Предприятие"
        
        # Создаем сессию
        session_token = generate_session_token()
        expires_at = datetime.now() + timedelta(hours=SESSION_EXPIRY_HOURS)
        
        await conn.execute(
            """INSERT INTO user_sessions (session_token, user_id, enterprise_number, expires_at)
               VALUES ($1, $2, $3, $4)""",
            session_token, user['id'], user['enterprise_number'], expires_at
        )
        
        # Удаляем использованный код
        await conn.execute("DELETE FROM auth_codes WHERE email = $1", email)
        
        logger.info(f"✅ Успешная авторизация для {email}, user_id: {user['id']}")
        
        # Формируем правильный URL для перенаправления на Рабочий стол
        redirect_url = f"{DESK_SERVICE_URL}/?enterprise={enterprise_name}&number={user['enterprise_number']}"
        
        return JSONResponse({
            "success": True,
            "session_token": session_token,
            "redirect_url": redirect_url
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка проверки кода: {e}")
        raise HTTPException(status_code=500, detail="Ошибка проверки кода")
    finally:
        await conn.close()

@app.get("/logout")
async def logout(request: Request, session_token: str = Cookie(None)):
    """Выход из системы"""
    if session_token:
        conn = await get_db_connection()
        if conn:
            try:
                await conn.execute(
                    "DELETE FROM user_sessions WHERE session_token = $1",
                    session_token
                )
                logger.info(f"🚪 Пользователь вышел из системы")
            except Exception as e:
                logger.error(f"Ошибка при выходе: {e}")
            finally:
                await conn.close()
    
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("session_token")
    return response

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {"status": "ok", "service": "auth", "timestamp": datetime.now().isoformat()}

# ══════════════════════════════════════════════════════════════════════════════
# ФОНОВЫЕ ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════════════════

async def background_cleanup():
    """Фоновая очистка истекших кодов и сессий"""
    while True:
        try:
            await cleanup_expired_codes()
            await cleanup_expired_sessions()
            await asyncio.sleep(300)  # Каждые 5 минут
        except Exception as e:
            logger.error(f"Ошибка фоновой очистки: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup():
    """Запуск фоновых задач"""
    logger.info("🚀 Запуск сервиса авторизации на порту 8015")
    asyncio.create_task(background_cleanup())

@app.on_event("shutdown")
async def shutdown():
    """Завершение работы"""
    logger.info("🛑 Остановка сервиса авторизации")

# ══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК СЕРВИСА
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "auth:app",
        host="0.0.0.0",
        port=8015,
        reload=False,
        log_level="info"
    ) 