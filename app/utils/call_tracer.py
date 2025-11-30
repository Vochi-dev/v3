"""
Call Tracer - логирование событий в файлы для каждого юнита.
Используется для логирования Asterisk и Telegram событий.
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict

# Словарь логгеров для call_tracer (по enterprise_number)
_call_tracer_loggers: Dict[str, logging.Logger] = {}

# Основной логгер для отладки
_module_logger = logging.getLogger("call_tracer")


def get_call_tracer_logger(enterprise_number: str) -> logging.Logger:
    """Возвращает логгер для юнита, создаёт папку и файл при необходимости."""
    if enterprise_number in _call_tracer_loggers:
        return _call_tracer_loggers[enterprise_number]
    
    # Создаём папку для юнита
    log_dir = f"call_tracer/{enterprise_number}"
    os.makedirs(log_dir, exist_ok=True)
    
    # Создаём логгер с ротацией по дням (14 дней)
    handler = TimedRotatingFileHandler(
        f"{log_dir}/events.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s|%(message)s"))
    handler.suffix = "%Y-%m-%d"  # Формат даты в имени файла
    
    tracer_logger = logging.getLogger(f"call_tracer_{enterprise_number}")
    tracer_logger.addHandler(handler)
    tracer_logger.setLevel(logging.INFO)
    tracer_logger.propagate = False
    
    _call_tracer_loggers[enterprise_number] = tracer_logger
    _module_logger.info(f"Created call_tracer logger for enterprise {enterprise_number}")
    return tracer_logger


def log_telegram_event(
    enterprise_number: str,
    action: str,           # send, edit, delete
    chat_id: int,
    message_type: str,     # start, dial, bridge, hangup
    message_id: int,
    unique_id: str,
    text: str = ""
):
    """
    Логирует Telegram событие в call_tracer.
    Формат: timestamp|TG|action|chat_id|message_type|message_id|unique_id|text
    """
    try:
        if not enterprise_number:
            return
        tracer = get_call_tracer_logger(enterprise_number)
        # Обрезаем текст до 200 символов и убираем переносы строк
        text_truncated = text[:200].replace('\n', ' ').replace('\r', '') if text else ""
        tracer.info(f"TG|{action}|{chat_id}|{message_type}|{message_id}|{unique_id}|{text_truncated}")
    except Exception as e:
        _module_logger.warning(f"Failed to log telegram event: {e}")


def log_asterisk_event(
    enterprise_number: str,
    event_type: str,
    unique_id: str,
    body: dict
):
    """
    Логирует Asterisk событие в call_tracer.
    Формат: timestamp|AST|event_type|unique_id|json_body
    """
    try:
        if not enterprise_number:
            return
        import json
        tracer = get_call_tracer_logger(enterprise_number)
        tracer.info(f"AST|{event_type}|{unique_id}|{json.dumps(body, ensure_ascii=False)}")
    except Exception as e:
        _module_logger.warning(f"Failed to log asterisk event: {e}")

