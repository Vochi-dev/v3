"""
Клиент для взаимодействия с Call Logger Service (порт 8026)
"""
import asyncio
import json
import time
from typing import Dict, Any, Optional
import httpx
import logging

logger = logging.getLogger(__name__)

class LoggerClient:
    """Клиент для отправки логов в Call Logger Service"""
    
    def __init__(self, base_url: str = "http://localhost:8026"):
        self.base_url = base_url
        self.timeout = 5.0  # 5 секунд таймаут
    
    async def log_call_event(
        self, 
        enterprise_number: str, 
        unique_id: str, 
        event_type: str, 
        event_data: Dict[str, Any],
        phone_number: Optional[str] = None,
        chat_id: Optional[int] = None,
        bridge_unique_id: Optional[str] = None,
        background: bool = False
    ) -> bool:
        """
        Логирование события звонка
        
        Args:
            enterprise_number: Номер предприятия (0367, 0280, etc.)
            unique_id: Уникальный ID звонка от Asterisk
            event_type: Тип события (dial, bridge, hangup, etc.)
            event_data: Данные события
            phone_number: Номер телефона (опционально)
            chat_id: ID чата в Telegram (опционально)
            bridge_unique_id: BridgeUniqueid для группировки событий (опционально)
            background: Если True, отправляет в фоне без ожидания (опционально)
        
        Returns:
            bool: True если успешно залогировано (или True если background=True)
        """
        payload = {
            "enterprise_number": enterprise_number,
            "unique_id": unique_id,
            "event_type": event_type,
            "event_data": event_data
        }
        
        if phone_number:
            payload["phone_number"] = phone_number
        if chat_id:
            payload["chat_id"] = chat_id
        if bridge_unique_id:
            payload["bridge_unique_id"] = bridge_unique_id
        
        if background:
            self._send_log_background("/log/event", payload)
            return True
        else:
            return await self._send_log("/log/event", payload)
    
    async def log_http_request(
        self,
        enterprise_number: str,
        unique_id: str,
        method: str,
        url: str,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        status_code: Optional[int] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Логирование HTTP запроса
        
        Args:
            enterprise_number: Номер предприятия
            unique_id: Уникальный ID звонка
            method: HTTP метод (GET, POST, etc.)
            url: URL запроса
            request_data: Данные запроса
            response_data: Данные ответа
            status_code: HTTP статус код
            duration_ms: Время выполнения в миллисекундах
            error: Текст ошибки если есть
        
        Returns:
            bool: True если успешно залогировано
        """
        payload = {
            "enterprise_number": enterprise_number,
            "unique_id": unique_id,
            "method": method,
            "url": url
        }
        
        if request_data is not None:
            payload["request_data"] = request_data
        if response_data is not None:
            payload["response_data"] = response_data
        if status_code is not None:
            payload["status_code"] = status_code
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error is not None:
            payload["error"] = error
            
        return await self._send_log("/log/http", payload)
    
    async def log_sql_query(
        self,
        enterprise_number: str,
        unique_id: str,
        query: str,
        parameters: Optional[list] = None,
        result: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Логирование SQL запроса
        
        Args:
            enterprise_number: Номер предприятия
            unique_id: Уникальный ID звонка
            query: SQL запрос
            parameters: Параметры запроса
            result: Результат запроса
            duration_ms: Время выполнения в миллисекундах
            error: Текст ошибки если есть
        
        Returns:
            bool: True если успешно залогировано
        """
        payload = {
            "enterprise_number": enterprise_number,
            "unique_id": unique_id,
            "query": query
        }
        
        if parameters is not None:
            payload["parameters"] = parameters
        if result is not None:
            payload["result"] = result
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error is not None:
            payload["error"] = error
            
        return await self._send_log("/log/sql", payload)
    
    async def log_telegram_message(
        self,
        enterprise_number: str,
        unique_id: str,
        chat_id: int,
        message_type: str,
        action: str,
        message_id: Optional[int] = None,
        message_text: Optional[str] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Логирование Telegram операции
        
        Args:
            enterprise_number: Номер предприятия
            unique_id: Уникальный ID звонка
            chat_id: ID чата в Telegram
            message_type: Тип сообщения (dial, bridge, hangup, etc.)
            action: Действие (send, edit, delete)
            message_id: ID сообщения в Telegram
            message_text: Текст сообщения
            error: Текст ошибки если есть
        
        Returns:
            bool: True если успешно залогировано
        """
        payload = {
            "enterprise_number": enterprise_number,
            "unique_id": unique_id,
            "chat_id": chat_id,
            "message_type": message_type,
            "action": action
        }
        
        if message_id is not None:
            payload["message_id"] = message_id
        if message_text is not None:
            payload["message_text"] = message_text
        if error is not None:
            payload["error"] = error
            
        return await self._send_log("/log/telegram", payload)
    
    async def log_integration_response(
        self,
        enterprise_number: str,
        unique_id: str,
        integration: str,
        endpoint: str,
        method: str,
        status: str,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Логирование ответа интеграции
        
        Args:
            enterprise_number: Номер предприятия
            unique_id: Уникальный ID звонка
            integration: Название интеграции (moysklad, retailcrm, etc.)
            endpoint: Endpoint API
            method: HTTP метод (GET, POST, PUT, DELETE)
            status: Статус (success, error, etc.)
            request_data: Данные запроса
            response_data: Данные ответа
            duration_ms: Время выполнения в миллисекундах
            error: Текст ошибки если есть
        
        Returns:
            bool: True если успешно залогировано
        """
        payload = {
            "enterprise_number": enterprise_number,
            "unique_id": unique_id,
            "integration": integration,
            "endpoint": endpoint,
            "method": method,
            "status": status
        }
        
        if request_data is not None:
            payload["request_data"] = request_data
        if response_data is not None:
            payload["response_data"] = response_data
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error is not None:
            payload["error"] = error
            
        return await self._send_log("/log/integration", payload)
    
    async def _send_log(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        """
        Отправка лога в Call Logger Service
        
        Args:
            endpoint: Эндпоинт API (/log/event, /log/http, etc.)
            payload: Данные для отправки
        
        Returns:
            bool: True если успешно отправлено
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}{endpoint}",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    return True
                else:
                    logger.warning(
                        f"Logger service returned {response.status_code}: {response.text}"
                    )
                    return False
                    
        except asyncio.TimeoutError:
            logger.warning(f"Timeout sending log to {endpoint}")
            return False
        except Exception as e:
            logger.warning(f"Error sending log to {endpoint}: {e}")
            return False
    
    def _send_log_background(self, endpoint: str, payload: Dict[str, Any]):
        """
        Отправка лога в фоне без ожидания ответа (fire-and-forget)
        
        Args:
            endpoint: Эндпоинт API (/log/event, /log/http, etc.)
            payload: Данные для отправки
        """
        asyncio.create_task(self._send_log(endpoint, payload))

# Глобальный экземпляр клиента
call_logger = LoggerClient()

# Декораторы для удобного логирования
def log_http_call(enterprise_number: str, unique_id: str):
    """
    Декоратор для автоматического логирования HTTP запросов
    
    Usage:
        @log_http_call("0367", unique_id)
        async def get_manager_data(url):
            # HTTP запрос
            return response
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            method = kwargs.get('method', 'GET')
            url = args[0] if args else kwargs.get('url', 'unknown')
            
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                
                # Логируем успешный запрос
                await call_logger.log_http_request(
                    enterprise_number=enterprise_number,
                    unique_id=unique_id,
                    method=method,
                    url=url,
                    response_data=result if isinstance(result, dict) else {"data": str(result)},
                    status_code=200,
                    duration_ms=duration_ms
                )
                
                return result
                
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                
                # Логируем ошибку
                await call_logger.log_http_request(
                    enterprise_number=enterprise_number,
                    unique_id=unique_id,
                    method=method,
                    url=url,
                    duration_ms=duration_ms,
                    error=str(e)
                )
                
                raise
        
        return wrapper
    return decorator

# Вспомогательные функции
async def log_call_start(enterprise_number: str, unique_id: str, event_data: Dict[str, Any]):
    """Быстрое логирование начала звонка"""
    phone = event_data.get('Phone') or event_data.get('phone')
    return await call_logger.log_call_event(
        enterprise_number=enterprise_number,
        unique_id=unique_id,
        event_type="dial",
        event_data=event_data,
        phone_number=phone
    )

async def log_call_bridge(enterprise_number: str, unique_id: str, event_data: Dict[str, Any]):
    """Быстрое логирование соединения"""
    return await call_logger.log_call_event(
        enterprise_number=enterprise_number,
        unique_id=unique_id,
        event_type="bridge",
        event_data=event_data
    )

async def log_call_hangup(enterprise_number: str, unique_id: str, event_data: Dict[str, Any]):
    """Быстрое логирование завершения звонка"""
    return await call_logger.log_call_event(
        enterprise_number=enterprise_number,
        unique_id=unique_id,
        event_type="hangup",
        event_data=event_data
    )

async def log_integration_call(
    enterprise_number: str,
    unique_id: str,
    integration: str,
    endpoint: str,
    method: str,
    status: str,
    response_data: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None
):
    """Быстрое логирование вызова интеграции"""
    return await call_logger.log_integration_response(
        enterprise_number=enterprise_number,
        unique_id=unique_id,
        integration=integration,
        endpoint=endpoint,
        method=method,
        status=status,
        response_data=response_data,
        duration_ms=duration_ms,
        error=error
    )
