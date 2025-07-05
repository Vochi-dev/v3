#!/usr/bin/env python3
"""
GoIP Микросервис для управления GoIP устройствами
Порт: 8008
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import aiohttp
import asyncpg
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
import re
# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация (жестко заданная для избежания конфликтов с основным приложением)
POSTGRES_HOST = 'localhost'
POSTGRES_PORT = 5432
POSTGRES_DB = 'postgres'
POSTGRES_USER = 'postgres'
POSTGRES_PASSWORD = 'r/Yskqh/ZbZuvjb2b3ahfg=='

GOIP_SERVICE_PORT = 8008
GOIP_SCAN_INTERVAL = 300  # 5 минут
GOIP_SCAN_TIMEOUT = 30

MFTP_HOST = 'mftp.vochi.by'
MFTP_PORT = 8086
MFTP_USERNAME = 'admin'
MFTP_PASSWORD = 'cdjkjxbdct4070+37529AAA'

GOIP_DEFAULT_USERNAME = 'admin'
GOIP_DEFAULT_PASSWORD = 'admin'

# FastAPI приложение
app = FastAPI(
    title="GoIP Management Service",
    description="Микросервис для управления GoIP устройствами",
    version="1.0.0"
)

# Добавляем CORS middleware для поддержки запросов из браузера
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене лучше указать конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Модели данных
class GoIPDevice(BaseModel):
    id: int
    gateway_name: str
    enterprise_number: str
    port: Optional[int] = None
    port_scan_status: str = 'unknown'
    device_model: Optional[str] = None
    serial_last4: Optional[str] = None
    line_count: int
    device_password: Optional[str] = None

class DeviceInfo(BaseModel):
    model: str
    serial_number: str
    serial_last4: str
    firmware_version: str
    module_version: str

class RebootRequest(BaseModel):
    gateway_name: str

class LineStatus(BaseModel):
    line: int
    auth_id: str
    rssi: Optional[str] = None
    busy_status: Optional[str] = None
    call_forward_busy: Optional[str] = None  # Переадресация при занятости

# Подключение к базе данных
async def get_db_connection():
    """Создание подключения к PostgreSQL"""
    return await asyncpg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD
    )

# Функции для работы с mftp
async def scan_mftp_for_ports() -> Dict[str, int]:
    """Сканирование mftp.vochi.by для поиска портов GoIP устройств"""
    try:
        url = f"http://{MFTP_USERNAME}:{MFTP_PASSWORD}@{MFTP_HOST}:{MFTP_PORT}"
        timeout = aiohttp.ClientTimeout(total=GOIP_SCAN_TIMEOUT)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to connect to mftp: {response.status}")
                    return {}
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Ищем ссылки RADMIN
                devices = {}
                for link in soup.find_all('a', href=True):
                    if link.text.strip() == 'RADMIN':
                        href = link['href']
                        # Извлекаем порт из URL вида http://mftp.vochi.by:38000
                        match = re.search(r':(\d+)$', href)
                        if match:
                            port = int(match.group(1))
                            # Ищем имя устройства в той же строке таблицы
                            row = link.find_parent('tr')
                            if row:
                                cells = row.find_all('td')
                                if len(cells) >= 2:
                                    device_name = cells[0].text.strip()
                                    devices[device_name] = port
                
                logger.info(f"Found {len(devices)} GoIP devices on mftp")
                return devices
                
    except Exception as e:
        logger.error(f"Error scanning mftp: {e}")
        return {}

async def get_device_info(port: int, password: str) -> Optional[DeviceInfo]:
    """Получение информации об устройстве через веб-интерфейс"""
    try:
        url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/config.html"
        timeout = aiohttp.ClientTimeout(total=GOIP_SCAN_TIMEOUT)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to connect to GoIP device on port {port}")
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Извлекаем информацию
                model = soup.title.string.strip() if soup.title else ""
                
                # Ищем серийный номер в таблице
                serial_number = ""
                firmware_version = ""
                module_version = ""
                
                for row in soup.find_all('tr'):
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        label = cells[0].text.strip()
                        value = cells[1].text.strip()
                        
                        if 'Serial Number' in label or 'SN' in label:
                            serial_number = value
                        elif 'Firmware Version' in label:
                            firmware_version = value
                        elif 'Module Version' in label:
                            module_version = value
                
                # Извлекаем последние 4 цифры серийного номера
                serial_last4 = ""
                if serial_number:
                    digits = ''.join(filter(str.isdigit, serial_number))
                    serial_last4 = digits[-4:] if len(digits) >= 4 else digits
                
                return DeviceInfo(
                    model=model,
                    serial_number=serial_number,
                    serial_last4=serial_last4,
                    firmware_version=firmware_version,
                    module_version=module_version
                )
                
    except Exception as e:
        logger.error(f"Error getting device info for port {port}: {e}")
        return None

async def get_device_line_status(port: int, password: str) -> List[LineStatus]:
    """Получение статуса линий устройства"""
    try:
        # Получаем Authentication ID из Basic VoIP
        auth_url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/config.html?type=calling"
        
        # Получаем RSSI из Status Summary
        status_url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/status.html"
        
        # Получаем Busy Status из SIM Call Forward
        busy_url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/status.html?type=callforward"
        
        # Получаем данные переадресации из SIM Call Forward
        forward_url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/status.html?type=callforward"
        
        timeout = aiohttp.ClientTimeout(total=GOIP_SCAN_TIMEOUT)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Получаем все данные параллельно
            auth_task = session.get(auth_url)
            status_task = session.get(status_url)
            busy_task = session.get(busy_url)
            
            auth_response, status_response, busy_response = await asyncio.gather(
                auth_task, status_task, busy_task, return_exceptions=True
            )
            
            lines = []
            
            # Обрабатываем результаты
            if not isinstance(auth_response, Exception) and auth_response.status == 200:
                auth_html = await auth_response.text()
                # Извлекаем Authentication ID для каждой линии
                for i in range(1, 33):  # Поддерживаем до 32 линий
                    pattern = f'name="sip_line{i}_auth_id"[^>]*value="([^"]*)"'
                    match = re.search(pattern, auth_html)
                    if match and match.group(1):
                        lines.append(LineStatus(line=i, auth_id=match.group(1)))
            
            # Добавляем RSSI данные
            if not isinstance(status_response, Exception) and status_response.status == 200:
                status_html = await status_response.text()
                for line_status in lines:
                    # Ищем содержимое ячейки с RSSI, включая HTML теги
                    # Паттерн для извлечения содержимого до закрывающего тега </td>
                    pattern = f'id="l{line_status.line}_gsm_signal"[^>]*>(.*?)</td>'
                    match = re.search(pattern, status_html, re.DOTALL)
                    
                    if match:
                        rssi_content = match.group(1).strip()
                        logger.info(f"📶 [RSSI] Найдено содержимое для линии {line_status.line}: '{rssi_content}'")
                        
                        # Очищаем от HTML entities
                        rssi_content = rssi_content.replace('&nbsp;', '').strip()
                        
                        # Сначала пробуем извлечь значение из HTML тега <font>
                        font_match = re.search(r'<font[^>]*>(\d+)</font>', rssi_content)
                        if font_match:
                            rssi_value = font_match.group(1)
                            logger.info(f"🎨 [RSSI] Найдено цветное значение для линии {line_status.line}: {rssi_value}")
                        else:
                            # Если нет цветного тега, ищем обычные цифры
                            rssi_digits = re.findall(r'\d+', rssi_content)
                            if rssi_digits:
                                rssi_value = rssi_digits[0]
                            else:
                                rssi_value = None
                        
                        if rssi_value and rssi_value.isdigit():
                            # Проверяем, что значение в допустимом диапазоне RSSI (0-31)
                            if 0 <= int(rssi_value) <= 31:
                                line_status.rssi = rssi_value
                                logger.info(f"✅ [RSSI] Линия {line_status.line}: установлено RSSI={rssi_value}")
                            else:
                                logger.warning(f"⚠️ [RSSI] Линия {line_status.line}: значение {rssi_value} вне диапазона 0-31")
                        else:
                            logger.warning(f"⚠️ [RSSI] Линия {line_status.line}: цифры не найдены в '{rssi_content}'")
                    else:
                        # Если не найдено, попробуем более широкий поиск
                        broad_pattern = f'l{line_status.line}_gsm_signal.*?>(.*?)<'
                        broad_match = re.search(broad_pattern, status_html, re.DOTALL)
                        if broad_match:
                            logger.warning(f"🔍 [RSSI] Широкий поиск для линии {line_status.line}: '{broad_match.group(1)[:100]}'")
                        else:
                            logger.error(f"❌ [RSSI] Не найден элемент l{line_status.line}_gsm_signal в HTML")
            
            # Добавляем Busy Status данные
            if not isinstance(busy_response, Exception) and busy_response.status == 200:
                busy_html = await busy_response.text()
                for line_status in lines:
                    # Получаем статус занятости
                    pattern = f'id="l{line_status.line}_cf_busy_status"[^>]*>([^<]*)<'
                    match = re.search(pattern, busy_html)
                    if match and match.group(1).strip() != '&nbsp;':
                        line_status.busy_status = match.group(1).strip()
                    
                    # Получаем номер переадресации при занятости из колонки "Busy"
                    # Ищем ячейку в таблице SIM Call Forward для данной линии
                    # Паттерн ищет строку таблицы с номером линии и извлекает значение из колонки "Busy"
                    busy_forward_pattern = rf'<tr[^>]*>.*?<td[^>]*>\s*{line_status.line}\s*</td>.*?<td[^>]*>.*?</td>.*?<td[^>]*>([^<]*)</td>'
                    busy_forward_match = re.search(busy_forward_pattern, busy_html, re.DOTALL | re.IGNORECASE)
                    if busy_forward_match:
                        busy_forward_value = busy_forward_match.group(1).strip()
                        # Очищаем от HTML entities и пробелов
                        busy_forward_value = re.sub(r'&nbsp;', '', busy_forward_value).strip()
                        if busy_forward_value and busy_forward_value != 'OFF' and busy_forward_value != 'Not Set':
                            line_status.call_forward_busy = busy_forward_value
            
            return lines
            
    except Exception as e:
        logger.error(f"Error getting line status for port {port}: {e}")
        return []

async def reboot_device(port: int, password: str) -> bool:
    """Перезагрузка GoIP устройства"""
    try:
        url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/tools.html"
        timeout = aiohttp.ClientTimeout(total=GOIP_SCAN_TIMEOUT)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Получаем страницу tools
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to access tools page on port {port}")
                    return False
                
                html = await response.text()
                
                # Ищем форму для reboot
                soup = BeautifulSoup(html, 'html.parser')
                reboot_form = None
                
                for form in soup.find_all('form'):
                    if 'reboot' in form.get('action', '').lower():
                        reboot_form = form
                        break
                
                if not reboot_form:
                    logger.error(f"Reboot form not found on port {port}")
                    return False
                
                # Отправляем POST запрос для перезагрузки
                form_data = {}
                for input_tag in reboot_form.find_all('input'):
                    name = input_tag.get('name')
                    value = input_tag.get('value', '')
                    if name:
                        form_data[name] = value
                
                reboot_url = f"http://{GOIP_DEFAULT_USERNAME}:{password}@{MFTP_HOST}:{port}/default/en_US/reboot.html"
                
                async with session.post(reboot_url, data=form_data) as reboot_response:
                    if reboot_response.status == 200:
                        logger.info(f"Device on port {port} rebooted successfully")
                        return True
                    else:
                        logger.error(f"Failed to reboot device on port {port}: {reboot_response.status}")
                        return False
                        
    except Exception as e:
        logger.error(f"Error rebooting device on port {port}: {e}")
        return False

# Функции для работы с базой данных
async def get_all_goip_devices() -> List[GoIPDevice]:
    """Получение всех GoIP устройств из базы данных"""
    conn = await get_db_connection()
    try:
        query = """
        SELECT 
            g.id, g.gateway_name, g.enterprise_number, g.port, 
            g.port_scan_status, g.device_model, g.serial_last4, g.line_count,
            e.secret as device_password
        FROM goip g 
        JOIN enterprises e ON g.enterprise_number = e.number 
        ORDER BY g.gateway_name
        """
        rows = await conn.fetch(query)
        return [GoIPDevice(**dict(row)) for row in rows]
    finally:
        await conn.close()

async def get_goip_device(gateway_name: str) -> Optional[GoIPDevice]:
    """Получение конкретного GoIP устройства"""
    conn = await get_db_connection()
    try:
        query = """
        SELECT 
            g.id, g.gateway_name, g.enterprise_number, g.port, 
            g.port_scan_status, g.device_model, g.serial_last4, g.line_count,
            e.secret as device_password
        FROM goip g 
        JOIN enterprises e ON g.enterprise_number = e.number 
        WHERE g.gateway_name = $1
        """
        row = await conn.fetchrow(query, gateway_name)
        return GoIPDevice(**dict(row)) if row else None
    finally:
        await conn.close()

async def update_device_port_info(device_id: int, port: int, status: str, device_info: Optional[DeviceInfo] = None):
    """Обновление информации о порте и устройстве"""
    conn = await get_db_connection()
    try:
        if device_info:
            query = """
            UPDATE goip 
            SET port = $1, port_scan_status = $2, last_port_scan = NOW(),
                device_model = $3, serial_last4 = $4
            WHERE id = $5
            """
            await conn.execute(query, port, status, device_info.model, device_info.serial_last4, device_id)
        else:
            query = """
            UPDATE goip 
            SET port = $1, port_scan_status = $2, last_port_scan = NOW()
            WHERE id = $3
            """
            await conn.execute(query, port, status, device_id)
    finally:
        await conn.close()

# Фоновые задачи
async def periodic_port_scan():
    """Периодическое сканирование портов GoIP устройств"""
    while True:
        try:
            logger.info("Starting periodic port scan...")
            
            # Получаем все устройства из БД
            devices = await get_all_goip_devices()
            
            # Сканируем mftp для поиска портов
            mftp_devices = await scan_mftp_for_ports()
            
            # Обновляем информацию о каждом устройстве
            for device in devices:
                if device.gateway_name in mftp_devices:
                    port = mftp_devices[device.gateway_name]
                    
                    # Получаем информацию об устройстве
                    device_info = await get_device_info(port, device.device_password)
                    
                    if device_info:
                        await update_device_port_info(device.id, port, 'active', device_info)
                        logger.info(f"Updated {device.gateway_name}: port {port}, model {device_info.model}")
                    else:
                        await update_device_port_info(device.id, port, 'error')
                        logger.warning(f"Failed to get info for {device.gateway_name} on port {port}")
                else:
                    await update_device_port_info(device.id, None, 'inactive')
                    logger.warning(f"Device {device.gateway_name} not found on mftp")
            
            logger.info("Periodic port scan completed")
            
        except Exception as e:
            logger.error(f"Error in periodic port scan: {e}")
        
        # Ждем до следующего сканирования
        await asyncio.sleep(GOIP_SCAN_INTERVAL)

# API endpoints
@app.on_event("startup")
async def startup_event():
    """Запуск фоновых задач при старте сервиса"""
    asyncio.create_task(periodic_port_scan())
    logger.info("GoIP Service started")

@app.get("/")
async def root():
    """Главная страница API"""
    return {"message": "GoIP Management Service", "version": "1.0.0"}

@app.get("/devices", response_model=List[GoIPDevice])
async def get_devices():
    """Получение списка всех GoIP устройств"""
    return await get_all_goip_devices()

@app.get("/devices/{gateway_name}", response_model=GoIPDevice)
async def get_device(gateway_name: str):
    """Получение информации о конкретном устройстве"""
    device = await get_goip_device(gateway_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device

@app.get("/devices/{gateway_name}/lines", response_model=List[LineStatus])
async def get_device_lines(gateway_name: str):
    """Получение статуса линий устройства"""
    device = await get_goip_device(gateway_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if not device.port or device.port_scan_status != 'active':
        raise HTTPException(status_code=400, detail="Device is not active or port unknown")
    
    lines = await get_device_line_status(device.port, device.device_password)
    return lines

@app.post("/devices/{gateway_name}/reboot")
async def reboot_device_endpoint(gateway_name: str):
    """Перезагрузка GoIP устройства"""
    device = await get_goip_device(gateway_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if not device.port or device.port_scan_status != 'active':
        raise HTTPException(status_code=400, detail="Device is not active or port unknown")
    
    success = await reboot_device(device.port, device.device_password)
    if success:
        return {"message": f"Device {gateway_name} rebooted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reboot device")

@app.post("/scan")
async def manual_scan():
    """Ручное сканирование портов"""
    devices = await get_all_goip_devices()
    mftp_devices = await scan_mftp_for_ports()
    
    results = []
    for device in devices:
        if device.gateway_name in mftp_devices:
            port = mftp_devices[device.gateway_name]
            device_info = await get_device_info(port, device.device_password)
            
            if device_info:
                await update_device_port_info(device.id, port, 'active', device_info)
                results.append({
                    "device": device.gateway_name,
                    "port": port,
                    "status": "active",
                    "model": device_info.model
                })
            else:
                await update_device_port_info(device.id, port, 'error')
                results.append({
                    "device": device.gateway_name,
                    "port": port,
                    "status": "error"
                })
        else:
            await update_device_port_info(device.id, None, 'inactive')
            results.append({
                "device": device.gateway_name,
                "status": "inactive"
            })
    
    return {"results": results}

@app.get("/enterprise/{enterprise_number}/devices")
async def get_enterprise_devices(enterprise_number: str):
    """Получение активных устройств предприятия"""
    try:
        conn = await get_db_connection()
        query = """
        SELECT 
            g.id, g.gateway_name, g.enterprise_number, g.port, 
            g.port_scan_status, g.device_model, g.serial_last4, g.line_count,
            e.secret as device_password
        FROM goip g 
        JOIN enterprises e ON g.enterprise_number = e.number 
        WHERE g.enterprise_number = $1 AND g.port_scan_status = 'active'
        ORDER BY g.gateway_name
        """
        rows = await conn.fetch(query, enterprise_number)
        devices = [GoIPDevice(**dict(row)) for row in rows]
        await conn.close()
        
        return {"devices": devices}
    except Exception as e:
        logger.error(f"Error getting enterprise devices: {e}")
        raise HTTPException(status_code=500, detail="Failed to get enterprise devices")

@app.get("/devices/{gateway_name}/info")
async def get_device_info_endpoint(gateway_name: str):
    """Получение информации об устройстве (серийный номер, uptime)"""
    logger.info(f"🔍 [API] Запрос информации об устройстве: {gateway_name}")
    
    device = await get_goip_device(gateway_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if not device.port or device.port_scan_status != 'active':
        raise HTTPException(status_code=400, detail="Device is not active or port unknown")
    
    try:
        # Получаем HTML страницу устройства (страница статуса)
        url = f"http://{MFTP_HOST}:{device.port}/default/en_US/status.html"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, auth=aiohttp.BasicAuth('admin', device.device_password)) as response:
                if response.status == 200:
                    html_content = await response.text()
                    logger.info(f"🌐 [API] Получен HTML для {gateway_name}, размер: {len(html_content)} символов")
                    
                    # Извлекаем серийный номер из таблицы
                    serial_number = None
                    serial_match = re.search(r'<td[^>]*>SN\(Serial Number\):</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.IGNORECASE)
                    if serial_match:
                        serial_number = serial_match.group(1).strip()
                    
                    # Извлекаем uptime из JavaScript
                    uptime_formatted = None
                    uptime_match = re.search(r'var uptime_s="(\d*)";', html_content)
                    if uptime_match and uptime_match.group(1):
                        uptime_seconds = int(uptime_match.group(1))
                        hours = uptime_seconds // 3600
                        minutes = (uptime_seconds % 3600) // 60
                        seconds = uptime_seconds % 60
                        uptime_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    
                    logger.info(f"🔍 [API] Для {gateway_name} найдено: SN={serial_number}, Uptime={uptime_formatted}")
                    
                    return {
                        "serial_number": serial_number,
                        "uptime": uptime_formatted
                    }
                else:
                    logger.error(f"❌ [API] Ошибка HTTP {response.status} для {gateway_name}")
                    raise HTTPException(status_code=response.status, detail=f"HTTP {response.status}")
    
    except Exception as e:
        logger.error(f"💥 [API] Ошибка получения информации об устройстве {gateway_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get device info")

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        # Проверяем подключение к БД
        conn = await get_db_connection()
        await conn.fetchval("SELECT 1")
        await conn.close()
        
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e), "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=GOIP_SERVICE_PORT) 