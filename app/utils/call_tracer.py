"""
Call Tracer - логирование событий в файлы для каждого юнита.
Используется для логирования Asterisk и Telegram событий.

ВАЖНО: Пишем напрямую в файл (append mode) без кэширования логгеров,
чтобы избежать проблем с несколькими воркерами uvicorn.
"""

import os
import json
from datetime import datetime

# Основной логгер для отладки
import logging
_module_logger = logging.getLogger("call_tracer")


def _write_to_log(enterprise_number: str, message: str):
    """Пишет сообщение напрямую в файл лога."""
    log_dir = f"call_tracer/{enterprise_number}"
    log_file = f"{log_dir}/events.log"
    
    # Создаём директорию если нет
    os.makedirs(log_dir, exist_ok=True)
    
    # Формируем строку с timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    line = f"{timestamp}|{message}\n"
    
    # Пишем в файл (append mode)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)


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
        # Обрезаем текст до 200 символов и убираем переносы строк
        text_truncated = text[:200].replace('\n', ' ').replace('\r', '') if text else ""
        message = f"TG|{action}|{chat_id}|{message_type}|{message_id}|{unique_id}|{text_truncated}"
        _write_to_log(enterprise_number, message)
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
        message = f"AST|{event_type}|{unique_id}|{json.dumps(body, ensure_ascii=False)}"
        _write_to_log(enterprise_number, message)
    except Exception as e:
        _module_logger.warning(f"Failed to log asterisk event: {e}")

