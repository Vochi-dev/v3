"""
Клиент для работы с кэшем метаданных и данными клиентов через сервис 8020
"""
import httpx
import logging
import time
import asyncio
from typing import Optional, Dict, Any, Tuple
import re

logger = logging.getLogger(__name__)

class MetadataClient:
    # TTL для кэша (в секундах)
    LINE_CACHE_TTL = 300      # 5 минут - линии меняются редко
    MANAGER_CACHE_TTL = 300   # 5 минут - менеджеры меняются редко
    CUSTOMER_CACHE_TTL = 60   # 1 минута - имена клиентов могут обновляться
    
    def __init__(self, base_url: str = "http://localhost:8020"):
        self.base_url = base_url.rstrip('/')
        
        # In-memory кэши: {cache_key: (data, timestamp)}
        self._line_cache: Dict[str, Tuple[Dict, float]] = {}
        self._manager_cache: Dict[str, Tuple[Dict, float]] = {}
        self._customer_cache: Dict[str, Tuple[str, float]] = {}
        
        # Shared HTTP client (будет создан при первом использовании)
        self._http_client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать shared HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=5.0)
        return self._http_client
    
    def _cache_get(self, cache: Dict, key: str, ttl: float) -> Optional[Any]:
        """Получить значение из кэша если не истёк TTL"""
        if key in cache:
            data, timestamp = cache[key]
            if time.time() - timestamp < ttl:
                return data
            else:
                # TTL истёк, удаляем
                del cache[key]
        return None
    
    def _cache_set(self, cache: Dict, key: str, data: Any):
        """Сохранить значение в кэш"""
        cache[key] = (data, time.time())
    
    async def _get_line_data(self, enterprise_number: str, line_id: str) -> Dict:
        """Получить данные линии с кэшированием"""
        cache_key = f"{enterprise_number}:{line_id}"
        
        # Проверяем кэш
        cached = self._cache_get(self._line_cache, cache_key, self.LINE_CACHE_TTL)
        if cached is not None:
            logger.debug(f"[CACHE HIT] line {cache_key}")
            return cached
        
        # Запрашиваем с сервера
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/metadata/{enterprise_number}/line/{line_id}")
            data = response.json() if response.status_code == 200 else {}
            
            # Сохраняем в кэш
            self._cache_set(self._line_cache, cache_key, data)
            logger.debug(f"[CACHE MISS] line {cache_key} - fetched from server")
            return data
        except Exception as e:
            logger.error(f"Error fetching line data: {e}")
            return {}
    
    async def _get_manager_data(self, enterprise_number: str, internal_phone: str) -> Dict:
        """Получить данные менеджера с кэшированием"""
        cache_key = f"{enterprise_number}:{internal_phone}"
        
        # Проверяем кэш
        cached = self._cache_get(self._manager_cache, cache_key, self.MANAGER_CACHE_TTL)
        if cached is not None:
            logger.debug(f"[CACHE HIT] manager {cache_key}")
            return cached
        
        # Запрашиваем с сервера
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}")
            data = response.json() if response.status_code == 200 else {}
            
            # Сохраняем в кэш (даже пустой результат, чтобы не запрашивать повторно)
            self._cache_set(self._manager_cache, cache_key, data)
            logger.debug(f"[CACHE MISS] manager {cache_key} - fetched from server")
            return data
        except Exception as e:
            logger.error(f"Error fetching manager data: {e}")
            return {}
    
    async def _get_customer_name_cached(self, enterprise_number: str, phone: str) -> str:
        """Получить имя клиента с кэшированием"""
        cache_key = f"{enterprise_number}:{phone}"
        
        # Проверяем кэш
        cached = self._cache_get(self._customer_cache, cache_key, self.CUSTOMER_CACHE_TTL)
        if cached is not None:
            logger.debug(f"[CACHE HIT] customer {cache_key}")
            return cached
        
        # Запрашиваем с сервера
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/customer-name/{enterprise_number}/{phone}")
            if response.status_code == 200:
                data = response.json()
                name = data.get("name", "Неизвестный")
            else:
                name = "Неизвестный"
            
            # Сохраняем в кэш
            self._cache_set(self._customer_cache, cache_key, name)
            logger.debug(f"[CACHE MISS] customer {cache_key} - fetched from server")
            return name
        except Exception as e:
            logger.error(f"Error fetching customer name: {e}")
            return "Неизвестный"
        
    async def get_line_name(self, enterprise_number: str, line_id: Optional[str]) -> str:
        """Получить название линии (с кэшированием)"""
        if not line_id:
            return "Неизвестная линия"
        
        data = await self._get_line_data(enterprise_number, line_id)
        return data.get("name", f"Линия {line_id}")
    
    async def get_manager_name(self, enterprise_number: str, internal_phone: Optional[str], short: bool = False) -> str:
        """Получить имя менеджера (с кэшированием)"""
        if not internal_phone:
            return "Неизвестный"
        
        data = await self._get_manager_data(enterprise_number, internal_phone)
        if short:
            return data.get("short_name", f"Доб.{internal_phone}")
        else:
            return data.get("full_name", f"Доб.{internal_phone}")
    
    async def get_manager_personal_phone(self, enterprise_number: str, internal_phone: Optional[str]) -> Optional[str]:
        """Получить личный номер менеджера (с кэшированием)"""
        if not internal_phone:
            return None
        
        data = await self._get_manager_data(enterprise_number, internal_phone)
        return data.get("personal_phone")
    
    async def get_manager_follow_me_number(self, enterprise_number: str, internal_phone: Optional[str]) -> Optional[str]:
        """Получить номер FollowMe менеджера (с кэшированием)"""
        if not internal_phone:
            return None
        
        data = await self._get_manager_data(enterprise_number, internal_phone)
        return data.get("follow_me_number")
    
    async def get_manager_follow_me_enabled(self, enterprise_number: str, internal_phone: Optional[str]) -> bool:
        """Проверить включен ли FollowMe у менеджера (с кэшированием)"""
        if not internal_phone:
            return False
        
        data = await self._get_manager_data(enterprise_number, internal_phone)
        return data.get("follow_me_enabled", False)
    
    async def get_manager_full_data(self, enterprise_number: str, internal_phone: Optional[str]) -> Dict[str, Any]:
        """Получить все данные менеджера (с кэшированием)"""
        if not internal_phone:
            return {}
        
        return await self._get_manager_data(enterprise_number, internal_phone)
    
    async def get_customer_name(self, enterprise_number: str, phone: Optional[str]) -> str:
        """Получить имя клиента (с кэшированием)"""
        if not phone:
            return "Неизвестный"
        
        return await self._get_customer_name_cached(enterprise_number, phone)
    
    async def get_customer_profile(self, enterprise_number: str, phone: Optional[str]) -> Dict[str, Any]:
        """Получить профиль клиента"""
        if not phone:
            return {}
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/customer-profile/{enterprise_number}/{phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"Failed to get customer profile for {phone}: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"Error getting customer profile: {e}")
            return {}
    
    async def enrich_message_data(self, enterprise_number: str, line_id: Optional[str] = None,
                                  internal_phone: Optional[str] = None, external_phone: Optional[str] = None,
                                  short_names: bool = False, unique_id: str = "") -> Dict[str, Any]:
        """
        Обогатить данные сообщения метаданными (ОПТИМИЗИРОВАНО: параллельные запросы + кэширование)
        
        Args:
            enterprise_number: Номер предприятия
            line_id: ID линии
            internal_phone: Внутренний номер
            external_phone: Внешний номер
            short_names: Использовать короткие имена менеджеров
            unique_id: UniqueId звонка для логирования
            
        Returns:
            Словарь с обогащенными данными:
            - line_name: Название линии
            - line_operator: Оператор линии  
            - manager_name: Имя менеджера
            - manager_personal_phone: Личный номер менеджера
            - customer_name: Имя клиента
        """
        enriched_data = {}
        
        # Собираем задачи для параллельного выполнения
        tasks = []
        task_names = []
        
        if line_id:
            tasks.append(self._get_line_data(enterprise_number, line_id))
            task_names.append("line")
        
        if internal_phone:
            tasks.append(self._get_manager_data(enterprise_number, internal_phone))
            task_names.append("manager")
        
        if external_phone:
            tasks.append(self._get_customer_name_cached(enterprise_number, external_phone))
            task_names.append("customer")
        
        # Выполняем все запросы ПАРАЛЛЕЛЬНО
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                task_name = task_names[i]
                
                if isinstance(result, Exception):
                    logger.error(f"Error in {task_name} enrichment: {result}")
                    continue
                
                if task_name == "line" and isinstance(result, dict):
                    enriched_data["line_name"] = result.get("name", f"Линия {line_id}")
                    enriched_data["line_operator"] = result.get("operator", "Unknown")
                
                elif task_name == "manager" and isinstance(result, dict):
                    if short_names:
                        enriched_data["manager_name"] = result.get("short_name", f"Доб.{internal_phone}")
                    else:
                        enriched_data["manager_name"] = result.get("full_name", f"Доб.{internal_phone}")
                    enriched_data["manager_personal_phone"] = result.get("personal_phone")
                
                elif task_name == "customer" and isinstance(result, str):
                    enriched_data["customer_name"] = result
        
        # Заполняем значения по умолчанию для отсутствующих данных
        if line_id and "line_name" not in enriched_data:
            enriched_data["line_name"] = f"Линия {line_id}"
            enriched_data["line_operator"] = "Unknown"
        
        if internal_phone and "manager_name" not in enriched_data:
            enriched_data["manager_name"] = f"Доб.{internal_phone}"
            enriched_data["manager_personal_phone"] = None
        
        if external_phone and "customer_name" not in enriched_data:
            enriched_data["customer_name"] = "Неизвестный"
        
        return enriched_data


def extract_internal_phone_from_channel(channel: str) -> Optional[str]:
    """
    Извлекает внутренний номер из канала Asterisk
    
    Примеры:
    - "SIP/150-00000001" -> "150"
    - "PJSIP/221-00000002" -> "221" 
    - "Local/150@from-internal-00000003;1" -> "150"
    """
    if not channel:
        return None
        
    # Паттерны для извлечения внутреннего номера
    patterns = [
        r'SIP/(\d+)-',           # SIP/150-00000001
        r'PJSIP/(\d+)-',         # PJSIP/221-00000002
        r'Local/(\d+)@',         # Local/150@from-internal-00000003;1
        r'/(\d+)-',              # Общий паттерн /<номер>-
    ]
    
    for pattern in patterns:
        match = re.search(pattern, channel)
        if match:
            return match.group(1)
    
    return None


def extract_line_id_from_exten(exten: str) -> Optional[str]:
    """
    Извлекает ID линии из Trunk/Extension
    
    Примеры:
    - "0001363" -> "0001363"
    - "GSM/0001363" -> "0001363"
    - "SIP/trunk_0001372" -> "0001372"
    """
    if not exten:
        return None
        
    # Ищем числовой ID линии
    match = re.search(r'(\d{7})', exten)  # 7-значный ID линии
    if match:
        return match.group(1)
    
    return None


# Глобальный экземпляр клиента
metadata_client = MetadataClient()