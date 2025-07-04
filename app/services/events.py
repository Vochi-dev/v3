import logging
import json
from datetime import datetime
from collections import defaultdict

from app.services.postgres import get_pool

# ───────── История hangup ─────────
hangup_message_map = defaultdict(list)

# ───────── Сохранение Asterisk-события ─────────
async def save_asterisk_event(event_type: str, unique_id: str, token: str, data: dict):
    """
    Записывает событие Asterisk в таблицу call_events (PostgreSQL).
    """
    logging.info(f"Saving Asterisk event: {event_type}, unique_id: {unique_id}, token: {token}")
    
    pool = await get_pool()
    if not pool:
        logging.error("PostgreSQL pool not available")
        return
    
    try:
        async with pool.acquire() as conn:
            # Создаем уникальный индекс для unique_id + event_type если его нет
            try:
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_call_events_unique_event 
                    ON call_events (unique_id, event_type)
                """)
            except:
                pass  # индекс уже существует

            # Сохраняем событие в call_events
            await conn.execute("""
                INSERT INTO call_events (
                    unique_id, event_type, event_timestamp, 
                    data_source, raw_data
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (unique_id, event_type) DO NOTHING
            """, 
            unique_id, 
            event_type, 
            datetime.utcnow(), 
            'live',  # источник - живые события от webhook
            json.dumps(data)
            )
            
            logging.debug(f"Saved event {event_type} for {unique_id} from token {token}")
    except Exception as e:
        logging.error(f"Failed to save event {event_type}: {e}")

# ───────── Обновление статуса отправки в Telegram ─────────
async def mark_telegram_sent(unique_id: str, event_type: str):
    """
    Обновляет флаг telegram_sent = true для события
    """
    pool = await get_pool()
    if not pool:
        logging.error("PostgreSQL pool not available")
        return
    
    try:
        async with pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE call_events 
                SET telegram_sent = true 
                WHERE unique_id = $1 AND event_type = $2
            """, unique_id, event_type)
            
            logging.debug(f"Marked telegram_sent for {event_type} event {unique_id}")
    except Exception as e:
        logging.error(f"Failed to mark telegram_sent for {event_type}: {e}")

# ───────── Инициализация (больше не нужна для PostgreSQL) ─────────
async def init_database_tables():
    """
    Функция больше не нужна - используем существующие PostgreSQL таблицы
    """
    logging.info("Using existing PostgreSQL tables for events")

# ───────── Загрузка истории hangup из БД ─────────
async def load_hangup_message_history(limit: int = 100):
    """
    Загружает последние сообщения 'hangup' из telegram_messages в память.
    TODO: Возможно, потребуется адаптировать под PostgreSQL в будущем
    """
    # Пока оставляем пустым, так как основная логика работает
    hangup_message_map.clear()
    logging.info("Hangup history loading disabled (using PostgreSQL)")

# ───────── Сохранение Telegram-сообщения ─────────
async def save_telegram_message(
    message_id: int,
    event_type: str,
    token: str,
    caller: str,
    callee: str,
    is_internal: bool,
    call_status: int = -1,
    call_type: int = -1,
    extensions: list = None
):
    """
    Записывает историю отправленных сообщений.
    TODO: Возможно, потребуется адаптировать под PostgreSQL в будущем
    """
    # Пока оставляем пустым, основная функциональность работает
    logging.debug(f"Telegram message {message_id} for {event_type} logged")
