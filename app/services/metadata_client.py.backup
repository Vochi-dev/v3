"""
Клиент для работы с кэшем метаданных и данными клиентов через сервис 8020
"""
import httpx
import logging
from typing import Optional, Dict, Any
import re

logger = logging.getLogger(__name__)

class MetadataClient:
    def __init__(self, base_url: str = "http://localhost:8020"):
        self.base_url = base_url.rstrip('/')
        
    async def get_line_name(self, enterprise_number: str, line_id: Optional[str]) -> str:
        """Получить название линии"""
        if not line_id:
            return "Неизвестная линия"
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/metadata/{enterprise_number}/line/{line_id}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("name", f"Линия {line_id}")
                else:
                    logger.warning(f"Failed to get line name for {line_id}: {response.status_code}")
                    return f"Линия {line_id}"
        except Exception as e:
            logger.error(f"Error getting line name: {e}")
            return f"Линия {line_id}"
    
    async def get_manager_name(self, enterprise_number: str, internal_phone: Optional[str], short: bool = False) -> str:
        """Получить имя менеджера"""
        if not internal_phone:
            return "Неизвестный"
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    if short:
                        return data.get("short_name", f"Доб.{internal_phone}")
                    else:
                        return data.get("full_name", f"Доб.{internal_phone}")
                else:
                    logger.warning(f"Failed to get manager name for {internal_phone}: {response.status_code}")
                    return f"Доб.{internal_phone}"
        except Exception as e:
            logger.error(f"Error getting manager name: {e}")
            return f"Доб.{internal_phone}"
    
    async def get_manager_personal_phone(self, enterprise_number: str, internal_phone: Optional[str]) -> Optional[str]:
        """Получить личный номер менеджера"""
        if not internal_phone:
            return None
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("personal_phone")
                else:
                    logger.warning(f"Failed to get manager personal phone for {internal_phone}: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"Error getting manager personal phone: {e}")
            return None
    
    async def get_manager_follow_me_number(self, enterprise_number: str, internal_phone: Optional[str]) -> Optional[str]:
        """Получить номер FollowMe менеджера"""
        if not internal_phone:
            return None
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("follow_me_number")
                else:
                    logger.warning(f"Failed to get manager follow me number for {internal_phone}: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"Error getting manager follow me number: {e}")
            return None
    
    async def get_manager_follow_me_enabled(self, enterprise_number: str, internal_phone: Optional[str]) -> bool:
        """Проверить включен ли FollowMe у менеджера"""
        if not internal_phone:
            return False
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("follow_me_enabled", False)
                else:
                    logger.warning(f"Failed to get manager follow me status for {internal_phone}: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Error getting manager follow me status: {e}")
            return False
    
    async def get_manager_full_data(self, enterprise_number: str, internal_phone: Optional[str]) -> Dict[str, Any]:
        """Получить все данные менеджера"""
        if not internal_phone:
            return {}
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"Failed to get manager data for {internal_phone}: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"Error getting manager data: {e}")
            return {}
    
    async def get_customer_name(self, enterprise_number: str, phone: Optional[str]) -> str:
        """Получить имя клиента"""
        if not phone:
            return "Неизвестный"
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/customer-name/{enterprise_number}/{phone}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("name", "Неизвестный")
                else:
                    logger.warning(f"Failed to get customer name for {phone}: {response.status_code}")
                    return "Неизвестный"
        except Exception as e:
            logger.error(f"Error getting customer name: {e}")
            return "Неизвестный"
    
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
        Обогатить данные сообщения метаданными
        
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
        from app.utils.call_tracer import log_http_request
        
        enriched_data = {}
        
        # Обогащаем данными линии
        if line_id:
            try:
                url = f"{self.base_url}/metadata/{enterprise_number}/line/{line_id}"
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=5.0)
                    line_data = response.json() if response.status_code == 200 else {}
                    
                    # Логируем HTTP запрос
                    log_http_request(
                        enterprise_number, unique_id, "GET", url,
                        response.status_code, {"line_id": line_id}, line_data
                    )
                    
                    if response.status_code == 200:
                        enriched_data["line_name"] = line_data.get("name", f"Линия {line_id}")
                        enriched_data["line_operator"] = line_data.get("operator", "Unknown")
                    else:
                        enriched_data["line_name"] = f"Линия {line_id}"
                        enriched_data["line_operator"] = "Unknown"
            except Exception as e:
                logger.error(f"Error enriching line data: {e}")
                enriched_data["line_name"] = f"Линия {line_id}"
                enriched_data["line_operator"] = "Unknown"
        
        # Обогащаем данными менеджера
        if internal_phone:
            try:
                url = f"{self.base_url}/metadata/{enterprise_number}/manager/{internal_phone}"
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=5.0)
                    manager_data = response.json() if response.status_code == 200 else {}
                    
                    # Логируем HTTP запрос
                    log_http_request(
                        enterprise_number, unique_id, "GET", url,
                        response.status_code, {"internal_phone": internal_phone}, manager_data
                    )
                    
                    if response.status_code == 200:
                        if short_names:
                            enriched_data["manager_name"] = manager_data.get("short_name", f"Доб.{internal_phone}")
                        else:
                            enriched_data["manager_name"] = manager_data.get("full_name", f"Доб.{internal_phone}")
                        enriched_data["manager_personal_phone"] = manager_data.get("personal_phone")
                    else:
                        enriched_data["manager_name"] = f"Доб.{internal_phone}"
                        enriched_data["manager_personal_phone"] = None
            except Exception as e:
                logger.error(f"Error enriching manager data: {e}")
                enriched_data["manager_name"] = f"Доб.{internal_phone}"
                enriched_data["manager_personal_phone"] = None
        
        # Обогащаем данными клиента
        if external_phone:
            try:
                url = f"{self.base_url}/customer-name/{enterprise_number}/{external_phone}"
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=5.0)
                    customer_data = response.json() if response.status_code == 200 else {}
                    
                    # Логируем HTTP запрос
                    log_http_request(
                        enterprise_number, unique_id, "GET", url,
                        response.status_code, {"phone": external_phone}, customer_data
                    )
                    
                    if response.status_code == 200:
                        enriched_data["customer_name"] = customer_data.get("name", "Неизвестный")
                    else:
                        enriched_data["customer_name"] = "Неизвестный"
            except Exception as e:
                logger.error(f"Error enriching customer data: {e}")
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