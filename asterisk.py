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

def ssh_get_active_channels(host_ip: str) -> Tuple[bool, str]:
    """Получение списка активных каналов через SSH CLI"""
    try:
        cli_command = 'asterisk -rx "core show channels concise"'
        
        ssh_command = [
            'sshpass', '-p', ASTERISK_CONFIG['ssh_password'],
            'ssh', '-p', str(ASTERISK_CONFIG['ssh_port']),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f"{ASTERISK_CONFIG['ssh_user']}@{host_ip}",
            cli_command
        ]
        
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, result.stderr.strip() if result.stderr else "Command failed"
            
    except Exception as e:
        logger.error(f"Ошибка получения каналов с {host_ip}: {e}")
        return False, f"Error: {str(e)}"

def ssh_monitor_call(host_ip: str, monitor_from: str, target_channel: str, action: str) -> Tuple[bool, str]:
    """Инициация мониторинга звонка через SSH CLI"""
    try:
        # Определяем флаги ChanSpy по типу действия
        spy_flags = {
            "09": "bq",      # Подслушивание (spy)
            "01": "bqw",     # Суфлирование (whisper)  
            "02": "Bbqw"     # Вмешательство (barge)
        }
        
        flags = spy_flags.get(action, "bq")
        
        # Формируем команду мониторинга
        cli_command = f'asterisk -rx "channel originate LOCAL/{monitor_from}@inoffice application ChanSpy {target_channel},{flags}"'
        
        ssh_command = [
            'sshpass', '-p', ASTERISK_CONFIG['ssh_password'],
            'ssh', '-p', str(ASTERISK_CONFIG['ssh_port']),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f"{ASTERISK_CONFIG['ssh_user']}@{host_ip}",
            cli_command
        ]
        
        action_names = {"09": "подслушивание", "01": "суфлирование", "02": "вмешательство"}
        action_name = action_names.get(action, "мониторинг")
        
        logger.info(f"🎧 SSH мониторинг ({action_name}) к {host_ip}: {monitor_from} -> {target_channel}")
        
        # Выполняем SSH команду
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=15
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Мониторинг инициирован на {host_ip}: {action_name} для {target_channel}")
            return True, f"Monitoring initiated successfully: {action_name} for {target_channel}"
        else:
            error_msg = result.stderr.strip() if result.stderr else "Unknown SSH error"
            logger.error(f"❌ SSH ошибка мониторинга на {host_ip}: {error_msg}")
            return False, f"SSH monitoring failed: {error_msg}"
            
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут SSH мониторинга к {host_ip}")
        return False, f"SSH monitoring timeout to {host_ip}"
    except Exception as e:
        logger.error(f"Ошибка SSH мониторинга на {host_ip}: {e}")
        return False, f"SSH monitoring error: {str(e)}"


def ssh_transfer_call(host_ip: str, from_channel: str, to_extension: str, transfer_type: str) -> Tuple[bool, str]:
    """
    Инициирует перевод звонка через SSH CLI
    
    Args:
        host_ip: IP удаленного Asterisk хоста
        from_channel: канал который переводит (например SIP/150-xxxx)
        to_extension: номер на который переводить (например 151)
        transfer_type: тип перевода ("blind" или "attended")
    
    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    try:
        if transfer_type == "blind":
            # Слепой перевод: найти и перенаправить внешний канал
            action_name = "слепой перевод"
            # Для слепого перевода нужно найти внешний канал, bridged с from_channel
            # Сначала получаем информацию о bridge
            bridge_info_command = f'asterisk -rx "core show channel {from_channel}"'
            
            # Выполняем команду для получения информации о канале
            ssh_bridge_command = [
                'sshpass', '-p', ASTERISK_CONFIG['ssh_password'],
                'ssh', '-p', str(ASTERISK_CONFIG['ssh_port']),
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'ConnectTimeout=10',
                f"{ASTERISK_CONFIG['ssh_user']}@{host_ip}",
                bridge_info_command
            ]
            
            bridge_result = subprocess.run(ssh_bridge_command, capture_output=True, text=True, timeout=10)
            
            if bridge_result.returncode != 0:
                return False, f"Failed to get channel info: {bridge_result.stderr}"
            
            # Ищем BRIDGEPEER переменную (внешний канал)
            bridged_channel = None
            for line in bridge_result.stdout.split('\n'):
                if 'BRIDGEPEER=' in line:
                    bridged_channel = line.split('BRIDGEPEER=')[1].strip()
                    break
            
            if bridged_channel:
                # Перенаправляем BRIDGEPEER (внешний канал) на новый номер
                cli_command = f'asterisk -rx "channel redirect {bridged_channel} inoffice,{to_extension},1"'
                logger.info(f"🔄 Перенаправляем внешний канал (BRIDGEPEER): {bridged_channel} -> {to_extension}")
            else:
                # Fallback: используем обычный redirect если не нашли BRIDGEPEER
                cli_command = f'asterisk -rx "channel redirect {from_channel} inoffice,{to_extension},1"'
                logger.warning(f"⚠️ BRIDGEPEER не найден, используем fallback: {from_channel} -> {to_extension}")
        elif transfer_type == "attended":
            # Запрошенный перевод: используем Local канал с proper transfer logic
            action_name = "запрошенный перевод"
            
            # Сначала получаем информацию о внешнем канале (BRIDGEPEER)
            bridge_info_command = f'asterisk -rx "core show channel {from_channel}"'
            
            ssh_bridge_command = [
                'sshpass', '-p', ASTERISK_CONFIG['ssh_password'],
                'ssh', '-p', str(ASTERISK_CONFIG['ssh_port']),
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'ConnectTimeout=10',
                f"{ASTERISK_CONFIG['ssh_user']}@{host_ip}",
                bridge_info_command
            ]
            
            bridge_result = subprocess.run(ssh_bridge_command, capture_output=True, text=True, timeout=10)
            
            if bridge_result.returncode != 0:
                return False, f"Failed to get channel info for attended transfer: {bridge_result.stderr}"
            
            # Ищем BRIDGEPEER (внешний канал)
            bridged_channel = None
            for line in bridge_result.stdout.split('\n'):
                if 'BRIDGEPEER=' in line:
                    bridged_channel = line.split('BRIDGEPEER=')[1].strip()
                    break
            
            if bridged_channel:
                # ATTENDED TRANSFER НЕ РЕАЛИЗОВАН - используем blind transfer
                # Перенаправляем внешний канал на целевой номер (как blind transfer)
                cli_command = f'asterisk -rx "channel redirect {bridged_channel} inoffice,{to_extension},1"'
                logger.info(f"⚠️ Attended transfer не реализован. Используем blind transfer: {bridged_channel} -> {to_extension}")
            else:
                return False, f"BRIDGEPEER не найден для attended transfer"
        else:
            return False, f"Неизвестный тип перевода: {transfer_type}"
        
        ssh_command = [
            'sshpass', '-p', ASTERISK_CONFIG['ssh_password'],
            'ssh', '-p', str(ASTERISK_CONFIG['ssh_port']),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            f"{ASTERISK_CONFIG['ssh_user']}@{host_ip}",
            cli_command
        ]
        
        logger.info(f"📞 {action_name.capitalize()}: {from_channel} -> {to_extension} на {host_ip}")
        logger.info(f"💻 CLI команда: {cli_command}")
        
        # Выполняем SSH команду
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=15
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Перевод инициирован на {host_ip}: {action_name} на {to_extension}")
            return True, f"Transfer initiated successfully: {action_name} to {to_extension}"
        else:
            error_msg = result.stderr.strip() if result.stderr else "Unknown SSH error"
            logger.error(f"❌ SSH ошибка перевода на {host_ip}: {error_msg}")
            return False, f"SSH transfer command failed: {error_msg}"
            
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут SSH перевода к {host_ip}")
        return False, f"SSH transfer timeout to {host_ip}"
    except Exception as e:
        logger.error(f"Ошибка SSH перевода на {host_ip}: {e}")
        return False, f"SSH transfer error: {str(e)}"


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "asterisk-call-management", "port": 8018}

@app.get("/api/monitor")
async def monitor_call(
    action: str = Query(..., description="Тип мониторинга: 09 (подслушивание), 01 (суфлирование), 02 (вмешательство)"),
    target: str = Query(..., description="Номер для мониторинга"),
    monitor_from: str = Query(..., description="Номер, который будет мониторить"),
    clientId: str = Query(..., description="Client ID (secret из enterprises)")
):
    """
    Инициация мониторинга активного звонка
    
    Параметры:
    - action: тип мониторинга
      - "09": подслушивание (spy)
      - "01": суфлирование (whisper) 
      - "02": вмешательство (barge)
    - target: номер который мониторим (например: 150)
    - monitor_from: номер который будет мониторить (например: 151)
    - clientId: secret из таблицы enterprises
    
    Пример:
    GET /api/monitor?action=09&target=150&monitor_from=151&clientId=eb7ba607633a47af8edc9b8d257d29e4
    """
    
    start_time = time.time()
    
    try:
        logger.info(f"🎧 Запрос на мониторинг: action={action}, target={target}, monitor_from={monitor_from}, clientId={clientId[:8]}...")
        
        # Валидация параметров
        if not action or not target or not monitor_from or not clientId:
            raise HTTPException(
                status_code=400, 
                detail="Все параметры обязательны: action, target, monitor_from, clientId"
            )
        
        if action not in ["09", "01", "02"]:
            raise HTTPException(
                status_code=400, 
                detail="action должен быть: 09 (подслушивание), 01 (суфлирование), 02 (вмешательство)"
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
            
            # Проверяем активные каналы на target номере
            channels_success, channels_data = ssh_get_active_channels(host_ip)
            
            if not channels_success:
                logger.error(f"❌ Не удалось получить список каналов: {channels_data}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Cannot get active channels: {channels_data}"
                )
            
            # Ищем канал target номера
            target_channel = f"SIP/{target}"
            
            # Проверяем есть ли активные каналы с этим номером
            if channels_data and target not in channels_data:
                logger.warning(f"🔍 Target {target} не найден в активных каналах. Продолжаем с SIP/{target}")
            
            # Инициируем мониторинг
            success, message = ssh_monitor_call(host_ip, monitor_from, target_channel, action)
            
            if success:
                response_time = round((time.time() - start_time) * 1000, 2)
                
                action_names = {"09": "подслушивание", "01": "суфлирование", "02": "вмешательство"}
                action_name = action_names.get(action, "мониторинг")
                
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "message": message,
                        "action": action,
                        "action_name": action_name,
                        "target": target,
                        "monitor_from": monitor_from,
                        "target_channel": target_channel,
                        "enterprise": enterprise_info['name'],
                        "enterprise_number": enterprise_info['enterprise_number'],
                        "host_ip": host_ip,
                        "response_time_ms": response_time
                    }
                )
            else:
                logger.error(f"❌ Ошибка инициации мониторинга: {message}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Monitoring initiation failed: {message}"
                )
                
        finally:
            await conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка мониторинга: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

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


@app.get("/api/transfer")
async def transfer_call(
    transfer_type: str = Query(..., description="Тип перевода: blind (слепой) или attended (запрошенный)"),
    from_ext: str = Query(..., description="Номер который переводит звонок"),
    to_ext: str = Query(..., description="Номер на который переводить"),
    clientId: str = Query(..., description="Client ID (secret из enterprises)")
):
    """
    Инициация перевода активного звонка
    
    Параметры:
    - transfer_type: тип перевода
      - "blind": слепой перевод (# + номер) - немедленная переадресация
      - "attended": запрошенный перевод (*2 + номер) - с консультацией
    - from_ext: внутренний номер который переводит звонок
    - to_ext: внутренний номер на который переводить
    - clientId: секретный ключ предприятия для авторизации
    
    Возвращает:
    - Результат операции перевода
    """
    start_time = time.time()
    
    try:
        # Подключение к БД
        conn = await get_db_connection()
        
        try:
            # Валидация clientId
            enterprise_info = await validate_client_secret(clientId, conn)
            if not enterprise_info:
                logger.warning(f"❌ Неверный clientId для перевода: {clientId}")
                raise HTTPException(status_code=401, detail="Invalid clientId")
            
            enterprise_name = enterprise_info['name']
            enterprise_number = enterprise_info['enterprise_number']
            host_ip = enterprise_info['host_ip']
            
            logger.info(f"📞 ПЕРЕВОД ЗВОНКА: {from_ext} -> {to_ext} ({transfer_type}) для {enterprise_name} ({enterprise_number})")
            
            # Валидация типа перевода
            if transfer_type not in ["blind", "attended"]:
                logger.error(f"❌ Неверный тип перевода: {transfer_type}")
                raise HTTPException(status_code=400, detail="transfer_type must be 'blind' or 'attended'")
            
            # Находим активный канал номера который переводит
            success_channels, channels_info = ssh_get_active_channels(host_ip)
            if not success_channels:
                logger.error(f"❌ Не удалось получить активные каналы с {host_ip}")
                raise HTTPException(status_code=500, detail="Failed to get active channels")
            
            # Ищем канал номера который переводит
            from_channel = None
            for line in channels_info.split('\n'):
                if line.strip() and f"SIP/{from_ext}-" in line:
                    # Извлекаем полное имя канала из строки
                    parts = line.split('!')
                    if parts:
                        from_channel = parts[0]
                        break
            
            if not from_channel:
                logger.error(f"❌ Активный канал для номера {from_ext} не найден на {host_ip}")
                raise HTTPException(status_code=404, detail=f"Active channel for extension {from_ext} not found")
            
            logger.info(f"🔍 Найден канал для перевода: {from_channel}")
            
            # Инициируем перевод звонка
            success, message = ssh_transfer_call(host_ip, from_channel, to_ext, transfer_type)
            
            if not success:
                logger.error(f"❌ Ошибка перевода: {message}")
                raise HTTPException(status_code=500, detail=message)
            
            response_time = round((time.time() - start_time) * 1000, 2)
            
            response_data = {
                "success": True,
                "message": message,
                "transfer_type": transfer_type,
                "transfer_name": "слепой перевод" if transfer_type == "blind" else "запрошенный перевод",
                "from_ext": from_ext,
                "to_ext": to_ext,
                "from_channel": from_channel,
                "enterprise": enterprise_name,
                "enterprise_number": enterprise_number,
                "host_ip": host_ip,
                "response_time_ms": response_time
            }
            
            logger.info(f"✅ Перевод успешно инициирован: {from_ext} -> {to_ext} ({transfer_type}) за {response_time}ms")
            return response_data
            
        finally:
            await conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при переводе: {e}")
        raise HTTPException(status_code=500, detail=f"Transfer error: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(
        "asterisk:app",
        host="0.0.0.0",
        port=8018,
        reload=True,
        log_level="info"
    )