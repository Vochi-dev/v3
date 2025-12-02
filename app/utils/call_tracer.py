"""
Call Tracer - логирование событий в файлы для каждого юнита.
Используется для логирования Asterisk и Telegram событий.

ВАЖНО: Пишем напрямую в файл (append mode) без кэширования логгеров,
чтобы избежать проблем с несколькими воркерами uvicorn.

Ротация: логи хранятся 14 дней, файлы именуются events_YYYY-MM-DD.log
"""

import os
import json
import glob
from datetime import datetime, timedelta

# Основной логгер для отладки
import logging
_module_logger = logging.getLogger("call_tracer")

# Количество дней хранения логов
LOG_RETENTION_DAYS = 14


def _cleanup_old_logs(log_dir: str):
    """Удаляет логи старше LOG_RETENTION_DAYS дней."""
    try:
        cutoff_date = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
        pattern = os.path.join(log_dir, "events_*.log")
        
        for log_file in glob.glob(pattern):
            # Извлекаем дату из имени файла events_YYYY-MM-DD.log
            filename = os.path.basename(log_file)
            try:
                date_str = filename.replace("events_", "").replace(".log", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff_date:
                    os.remove(log_file)
                    _module_logger.info(f"Removed old log file: {log_file}")
            except ValueError:
                # Не удалось распарсить дату - пропускаем
                pass
    except Exception as e:
        _module_logger.warning(f"Failed to cleanup old logs: {e}")


def _write_to_log(enterprise_number: str, message: str):
    """Пишет сообщение напрямую в файл лога с ротацией по дням."""
    log_dir = f"call_tracer/{enterprise_number}"
    
    # Файл с датой: events_2025-12-02.log
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = f"{log_dir}/events_{today}.log"
    
    # Создаём директорию если нет
    os.makedirs(log_dir, exist_ok=True)
    
    # Формируем строку с timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    line = f"{timestamp}|{message}\n"
    
    # Пишем в файл (append mode)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)
    
    # Периодически чистим старые логи (раз в ~1000 записей, чтобы не делать это каждый раз)
    # Используем простую проверку по размеру файла
    try:
        if os.path.getsize(log_file) < 1000:  # Только в начале дня (маленький файл)
            _cleanup_old_logs(log_dir)
    except:
        pass


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
        # Обрезаем текст до 1000 символов и убираем переносы строк
        text_truncated = text[:1000].replace('\n', ' ').replace('\r', '') if text else ""
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


def log_http_request(
    enterprise_number: str,
    unique_id: str,
    method: str,
    url: str,
    status_code: int,
    request_data: dict = None,
    response_data: dict = None
):
    """
    Логирует HTTP запрос в call_tracer.
    Формат: timestamp|HTTP|unique_id|method|url|status_code|request_json|response_json
    """
    try:
        if not enterprise_number:
            return
        req_json = json.dumps(request_data, ensure_ascii=False) if request_data else "{}"
        resp_json = json.dumps(response_data, ensure_ascii=False) if response_data else "{}"
        # Обрезаем длинные ответы
        if len(resp_json) > 500:
            resp_json = resp_json[:500] + "..."
        message = f"HTTP|{unique_id}|{method}|{url}|{status_code}|{req_json}|{resp_json}"
        _write_to_log(enterprise_number, message)
    except Exception as e:
        _module_logger.warning(f"Failed to log http request: {e}")


def log_sql_query(
    enterprise_number: str,
    unique_id: str,
    query_type: str,      # SELECT, INSERT, UPDATE, etc.
    table: str,
    params: dict = None,
    result: dict = None
):
    """
    Логирует SQL запрос в call_tracer.
    Формат: timestamp|SQL|unique_id|query_type|table|params_json|result_json
    """
    try:
        if not enterprise_number:
            return
        params_json = json.dumps(params, ensure_ascii=False) if params else "{}"
        result_json = json.dumps(result, ensure_ascii=False) if result else "{}"
        # Обрезаем длинные результаты
        if len(result_json) > 500:
            result_json = result_json[:500] + "..."
        message = f"SQL|{unique_id}|{query_type}|{table}|{params_json}|{result_json}"
        _write_to_log(enterprise_number, message)
    except Exception as e:
        _module_logger.warning(f"Failed to log sql query: {e}")

