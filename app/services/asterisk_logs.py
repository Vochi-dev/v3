import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from app.services.postgres import get_pool

async def determine_event_type(data: Dict[str, Any]) -> str:
    """Определяет тип события на основе данных"""
    if 'StartTime' in data and 'EndTime' in data:
        return 'hangup'
    elif 'Extensions' in data and 'CallerIDNum' not in data:
        return 'dial'
    else:
        return 'start'

async def is_duplicate(conn, unique_id: str, event_type: str) -> bool:
    """Проверяет, не является ли событие дубликатом"""
    # Дедупликация временно отключена
    return False

async def save_asterisk_log(data: Dict[str, Any]) -> None:
    """Сохраняет лог Asterisk в базу данных"""
    # Получаем обязательные поля
    token = data.get('Token')
    unique_id = data.get('UniqueId')
    
    if not token or not unique_id:
        raise ValueError("Token and UniqueId are required fields")

    # Определяем тип события
    event_type = await determine_event_type(data)
    
    # Сохраняем в базу с учетом таймзоны GMT+3
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем на дубликат
        if await is_duplicate(conn, unique_id, event_type):
            return

        await conn.execute("""
            INSERT INTO asterisk_logs (
                unique_id, token, event_type, raw_data, timestamp
            ) VALUES (
                $1, $2, $3, $4, 
                (CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AT TIME ZONE 'GMT+3')
            )
        """, unique_id, token, event_type, json.dumps(data))

async def get_call_history(token: str, limit: int = 100) -> list:
    """Получает историю звонков для предприятия"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (unique_id)
                timestamp AT TIME ZONE 'GMT+3' as timestamp,
                unique_id, event_type, raw_data
            FROM asterisk_logs
            WHERE token = $1
            ORDER BY unique_id, timestamp DESC
            LIMIT $2
        """, token, limit)
        return [dict(row) for row in rows]

async def get_call_details(unique_id: str) -> list:
    """Получает все события для конкретного звонка"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                timestamp AT TIME ZONE 'GMT+3' as timestamp,
                event_type, raw_data
            FROM asterisk_logs
            WHERE unique_id = $1
            ORDER BY timestamp ASC
        """, unique_id)
        return [dict(row) for row in rows] 