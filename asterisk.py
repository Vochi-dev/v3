# -*- coding: utf-8 -*-
"""
Asterisk Call Management Service
Сервис для управления звонками на удаленных Asterisk хостах
Порт: 8018
"""

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
import asyncpg
import logging
import subprocess
import time
from typing import Dict, Optional, Tuple
import asyncio
from datetime import datetime

from app.config import POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Asterisk Call Management API",
    description="Сервис для управления звонками через удаленные Asterisk хосты",
    version="1.0.0"
)

# Конфигурация БД
DB_CONFIG = {
    "user": POSTGRES_USER,
    "password": POSTGRES_PASSWORD,
    "database": POSTGRES_DB,
    "host": POSTGRES_HOST,
    "port": POSTGRES_PORT,
}

# Конфигурация удаленных Asterisk хостов
ASTERISK_CONFIG = {
    "ssh_port": 5059,
    "ssh_user": "root",
    "ssh_password": "5atx9Ate@pbx"
}

async def get_db_connection():
    """Получение подключения к БД"""
    try:
        return await asyncpg.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise HTTPException(status_code=500, detail="Database connection error")

async def validate_client_secret(client_id: str, conn: asyncpg.Connection) -> Optional[Dict]:
    """Проверка clientId против secret из таблицы enterprises"""
    try:
        query = """
        SELECT number, name, ip 
        FROM enterprises 
        WHERE secret = $1 AND active = true
        """
        result = await conn.fetchrow(query, client_id)
        
        if result:
            return {
                "enterprise_number": result["number"],
                "name": result["name"],
                "host_ip": result["ip"]
            }
        return None
        
    except Exception as e:
        logger.error(f"Ошибка проверки clientId: {e}")
        return None

def ssh_originate_call(host_ip: str, from_ext: str, to_phone: str) -> Tuple[bool, str]:
    """Инициация звонка через SSH CLI команды"""
    try:
        # Формируем SSH команду
        cli_command = f'asterisk -rx "channel originate LOCAL/{from_ext}@inoffice application Dial LOCAL/{to_phone}@inoffice"'
        
        ssh_command = [
            'sshpass', '-p', ASTERISK_CONFIG['ssh_password'],
            'ssh', '-p', str(ASTERISK_CONFIG['ssh_port']),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f"{ASTERISK_CONFIG['ssh_user']}@{host_ip}",
            cli_command
        ]
        
        logger.info(f"🔗 SSH подключение к {host_ip}: {from_ext} -> {to_phone}")
        
        # Выполняем SSH команду
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=15
        )
        
        if result.returncode == 0:
            logger.info(f"✅ CLI команда выполнена на {host_ip}: {from_ext} -> {to_phone}")
            # CLI команда не возвращает детального ответа, но если returncode = 0, значит команда прошла
            return True, f"Call initiated successfully: {from_ext} -> {to_phone}"
        else:
            error_msg = result.stderr.strip() if result.stderr else "Unknown SSH error"
            logger.error(f"❌ SSH ошибка на {host_ip}: {error_msg}")
            return False, f"SSH command failed: {error_msg}"
            
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут SSH подключения к {host_ip}")
        return False, f"SSH timeout to {host_ip}"
    except FileNotFoundError:
        logger.error("sshpass не установлен в системе")
        return False, "SSH client (sshpass) not available"
    except Exception as e:
        logger.error(f"Ошибка SSH на {host_ip}: {e}")
        return False, f"SSH error: {str(e)}"

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "asterisk-call-management", "port": 8018}

@app.get("/api/makecallexternal")
async def make_call_external(
    code: str = Query(..., description="Внутренний номер"),
    phone: str = Query(..., description="Номер телефона"),
    clientId: str = Query(..., description="Client ID (secret из enterprises)")
):
    """
    Инициация внешнего звонка
    
    Параметры:
    - code: внутренний номер (например: 150, 151)
    - phone: номер телефона (например: +375296254070)
    - clientId: secret из таблицы enterprises
    
    Пример:
    GET /api/makecallexternal?code=150&phone=+375296254070&clientId=eb7ba607633a47af8edc9b8d257d29e4
    """
    
    start_time = time.time()
    
    try:
        logger.info(f"🚀 Запрос на звонок: {code} -> {phone}, clientId: {clientId[:8]}...")
        
        # Валидация параметров
        if not code or not phone or not clientId:
            raise HTTPException(
                status_code=400, 
                detail="Все параметры обязательны: code, phone, clientId"
            )
        
        # Подключение к БД
        conn = await get_db_connection()
        
        try:
            # Проверяем clientId
            enterprise_info = await validate_client_secret(clientId, conn)
            
            if not enterprise_info:
                logger.warning(f"❌ Неверный clientId: {clientId}")
                raise HTTPException(
                    status_code=401, 
                    detail="Invalid clientId"
                )
            
            logger.info(f"✅ Клиент авторизован: {enterprise_info['name']} ({enterprise_info['enterprise_number']})")
            
            # Проверяем наличие host_ip
            host_ip = enterprise_info.get("host_ip")
            if not host_ip:
                logger.error(f"❌ Не указан host_ip для предприятия {enterprise_info['enterprise_number']}")
                raise HTTPException(
                    status_code=500, 
                    detail="Host IP not configured for this enterprise"
                )
            
            # Инициируем звонок через SSH CLI
            success, message = ssh_originate_call(host_ip, code, phone)
            
            if success:
                # Логируем успешный звонок в БД (опционально)
                try:
                    # Проверяем существование таблицы call_logs
                    table_exists = await conn.fetchval("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = 'public' 
                            AND table_name = 'call_logs'
                        )
                    """)
                    
                    if table_exists:
                        log_query = """
                        INSERT INTO call_logs (enterprise_number, from_ext, to_phone, status, created_at)
                        VALUES ($1, $2, $3, $4, $5)
                        """
                        await conn.execute(
                            log_query,
                            enterprise_info['enterprise_number'],
                            code,
                            phone,
                            'initiated',
                            datetime.now()
                        )
                    else:
                        logger.info("Таблица call_logs не существует, пропускаем логирование")
                        
                except Exception as log_error:
                    logger.warning(f"Ошибка логирования звонка: {log_error}")
                
                response_time = round((time.time() - start_time) * 1000, 2)
                
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "message": message,
                        "enterprise": enterprise_info['name'],
                        "enterprise_number": enterprise_info['enterprise_number'],
                        "from_ext": code,
                        "to_phone": phone,
                        "host_ip": host_ip,
                        "response_time_ms": response_time
                    }
                )
            else:
                logger.error(f"❌ Ошибка инициации звонка: {message}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Call initiation failed: {message}"
                )
                
        finally:
            await conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/api/status")
async def api_status():
    """Статус API и подключений"""
    try:
        # Проверяем подключение к БД
        conn = await get_db_connection()
        db_status = "connected"
        await conn.close()
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "service": "asterisk-call-management",
        "version": "1.0.0",
        "database": db_status,
        "asterisk_config": {
            "method": "SSH CLI",
            "ssh_port": ASTERISK_CONFIG["ssh_port"],
            "ssh_user": ASTERISK_CONFIG["ssh_user"]
        }
    }

if __name__ == "__main__":
    uvicorn.run(
        "asterisk:app",
        host="0.0.0.0",
        port=8018,
        reload=True,
        log_level="info"
    )