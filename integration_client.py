#!/usr/bin/env python3
"""
Integration Client Library

Библиотека для быстрого доступа к кэшу интеграций из call-сервисов.
Используется в dial.py, bridge.py, hangup.py, download.py.

Функциональность:
- Быстрая проверка активных интеграций
- Fallback на БД при недоступности кэша
- Автоматические ретраи и таймауты
- Метрики производительности
"""

import asyncio
import aiohttp
import asyncpg
import logging
import time
import os
from typing import Dict, Optional, Set, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Конфигурация
INTEGRATION_CACHE_URL = "http://127.0.0.1:8020"
CACHE_TIMEOUT = 2.0  # 2 секунды таймаут для кэша
DB_TIMEOUT = 5.0     # 5 секунд таймаут для БД
RETRY_COUNT = 2      # Количество попыток

@dataclass
class IntegrationStatus:
    """Статус интеграций для предприятия"""
    enterprise_number: str
    integrations: Dict[str, bool]
    source: str  # 'cache' или 'database'
    age_seconds: float = 0.0

class IntegrationClient:
    """Клиент для работы с кэшем интеграций"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.pg_pool: Optional[asyncpg.Pool] = None
        self.stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "db_fallbacks": 0,
            "errors": 0,
            "total_requests": 0
        }
    
    async def init(self):
        """Инициализация клиента"""
        # HTTP сессия для запросов к кэшу
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=CACHE_TIMEOUT),
            connector=aiohttp.TCPConnector(limit=10, limit_per_host=5)
        )
        
        # Пул подключений к БД для fallback
        try:
            password = os.environ.get('DB_PASSWORD', 'r/Yskqh/ZbZuvjb2b3ahfg==')
            self.pg_pool = await asyncpg.create_pool(
                host='localhost',
                port=5432,
                user='postgres',
                password=password,
                database='postgres',
                min_size=1,
                max_size=3,
                timeout=DB_TIMEOUT
            )
            logger.info("✅ Integration client initialized")
        except Exception as e:
            logger.warning(f"⚠️ Database pool init failed: {e}")
    
    async def close(self):
        """Закрытие клиента"""
        if self.session:
            await self.session.close()
        if self.pg_pool:
            await self.pg_pool.close()
    
    async def get_integrations(self, enterprise_number: str) -> IntegrationStatus:
        """
        Получить статус интеграций для предприятия
        
        Алгоритм:
        1. Попытка получить из кэша (быстро)
        2. При ошибке - fallback на БД (медленно)
        3. Возврат результата с метаинформацией
        """
        self.stats["total_requests"] += 1
        start_time = time.time()
        
        # Попытка получить из кэша
        try:
            cache_result = await self._get_from_cache(enterprise_number)
            if cache_result:
                self.stats["cache_hits"] += 1
                elapsed = time.time() - start_time
                logger.debug(f"📊 Cache hit for {enterprise_number} in {elapsed:.3f}s")
                return cache_result
        except Exception as e:
            logger.warning(f"⚠️ Cache error for {enterprise_number}: {e}")
        
        # Fallback на БД
        self.stats["cache_misses"] += 1
        self.stats["db_fallbacks"] += 1
        
        try:
            db_result = await self._get_from_database(enterprise_number)
            elapsed = time.time() - start_time
            logger.warning(f"🐌 DB fallback for {enterprise_number} in {elapsed:.3f}s")
            return db_result
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"❌ Both cache and DB failed for {enterprise_number}: {e}")
            
            # Возвращаем пустой результат
            return IntegrationStatus(
                enterprise_number=enterprise_number,
                integrations={},
                source="error",
                age_seconds=0.0
            )
    
    async def _get_from_cache(self, enterprise_number: str) -> Optional[IntegrationStatus]:
        """Получение из кэша"""
        if not self.session:
            return None
        
        for attempt in range(RETRY_COUNT):
            try:
                async with self.session.get(
                    f"{INTEGRATION_CACHE_URL}/integrations/{enterprise_number}"
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return IntegrationStatus(
                            enterprise_number=enterprise_number,
                            integrations=data["integrations"],
                            source="cache",
                            age_seconds=data.get("age_seconds", 0.0)
                        )
                    elif response.status == 404:
                        # Предприятие не найдено
                        return IntegrationStatus(
                            enterprise_number=enterprise_number,
                            integrations={},
                            source="cache",
                            age_seconds=0.0
                        )
                    else:
                        logger.warning(f"⚠️ Cache returned {response.status} for {enterprise_number}")
                        return None
                        
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Cache timeout for {enterprise_number} (attempt {attempt + 1})")
                if attempt == RETRY_COUNT - 1:
                    return None
                await asyncio.sleep(0.1 * (attempt + 1))  # Exponential backoff
            except Exception as e:
                logger.warning(f"⚠️ Cache error for {enterprise_number}: {e}")
                return None
    
    async def _get_from_database(self, enterprise_number: str) -> IntegrationStatus:
        """Получение из БД (fallback)"""
        if not self.pg_pool:
            raise Exception("Database pool not initialized")
        
        async with self.pg_pool.acquire() as conn:
            query = """
            SELECT integrations_config 
            FROM enterprises 
            WHERE number = $1 AND active = true
            """
            row = await conn.fetchrow(query, enterprise_number)
            
            if not row or not row['integrations_config']:
                return IntegrationStatus(
                    enterprise_number=enterprise_number,
                    integrations={},
                    source="database",
                    age_seconds=0.0
                )
            
            # Парсим включённые интеграции
            integrations_config = row['integrations_config']
            enabled_integrations = {}
            
            if integrations_config:
                for integration_type, config in integrations_config.items():
                    if isinstance(config, dict):
                        enabled_integrations[integration_type] = config.get('enabled', False)
            
            return IntegrationStatus(
                enterprise_number=enterprise_number,
                integrations=enabled_integrations,
                source="database",
                age_seconds=0.0
            )
    
    async def is_integration_enabled(self, enterprise_number: str, integration_type: str) -> bool:
        """Быстрая проверка включена ли интеграция"""
        status = await self.get_integrations(enterprise_number)
        return status.integrations.get(integration_type, False)
    
    async def get_enabled_integrations(self, enterprise_number: str) -> List[str]:
        """Получить список включённых интеграций"""
        status = await self.get_integrations(enterprise_number)
        return [
            integration_type 
            for integration_type, enabled in status.integrations.items() 
            if enabled
        ]
    
    def get_stats(self) -> Dict[str, any]:
        """Получить статистику клиента"""
        total = max(1, self.stats["total_requests"])
        cache_hit_rate = self.stats["cache_hits"] / total * 100
        
        return {
            **self.stats,
            "cache_hit_rate_percent": round(cache_hit_rate, 2)
        }

# Глобальный экземпляр клиента
_client: Optional[IntegrationClient] = None

async def get_client() -> IntegrationClient:
    """Получить глобальный экземпляр клиента"""
    global _client
    if not _client:
        _client = IntegrationClient()
        await _client.init()
    return _client

async def close_client():
    """Закрыть глобальный клиент"""
    global _client
    if _client:
        await _client.close()
        _client = None

# Удобные функции для использования в call-сервисах

async def is_retailcrm_enabled(enterprise_number: str) -> bool:
    """Проверить включен ли RetailCRM для предприятия"""
    client = await get_client()
    return await client.is_integration_enabled(enterprise_number, "retailcrm")

async def is_amocrm_enabled(enterprise_number: str) -> bool:
    """Проверить включен ли AmoCRM для предприятия"""
    client = await get_client()
    return await client.is_integration_enabled(enterprise_number, "amocrm")

async def get_enabled_crm_integrations(enterprise_number: str) -> List[str]:
    """Получить список включённых CRM интеграций"""
    client = await get_client()
    return await client.get_enabled_integrations(enterprise_number)

async def send_to_enabled_integrations(enterprise_number: str, event_type: str, payload: dict):
    """
    Отправить событие во все включённые интеграции
    
    Использование в call-сервисах:
    
    # В dial.py, bridge.py, hangup.py
    await send_to_enabled_integrations(enterprise_number, "dial", {
        "phone": "+375296254070",
        "extension": "150",
        "timestamp": "2025-08-09T10:30:00Z"
    })
    """
    enabled_integrations = await get_enabled_crm_integrations(enterprise_number)
    
    if not enabled_integrations:
        logger.debug(f"📭 No integrations enabled for {enterprise_number}")
        return
    
    # Асинхронная отправка во все включённые интеграции
    tasks = []
    
    for integration_type in enabled_integrations:
        if integration_type == "retailcrm":
            task = _send_to_retailcrm(enterprise_number, event_type, payload)
            tasks.append(task)
        elif integration_type == "amocrm":
            task = _send_to_amocrm(enterprise_number, event_type, payload)
            tasks.append(task)
    
    if tasks:
        # Fire-and-forget: не ждем завершения
        asyncio.create_task(_execute_integration_tasks(tasks, enterprise_number, event_type))

async def _execute_integration_tasks(tasks: List, enterprise_number: str, event_type: str):
    """Выполнить задачи интеграции в фоне"""
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.debug(f"📤 Sent {event_type} to {len(tasks)} integrations for {enterprise_number}")
    except Exception as e:
        logger.error(f"❌ Error sending {event_type} to integrations for {enterprise_number}: {e}")

async def _send_to_retailcrm(enterprise_number: str, event_type: str, payload: dict):
    """Отправка события в RetailCRM"""
    try:
        client = await get_client()
        if not client.session:
            return
        
        url = "http://127.0.0.1:8019/retailcrm/api/events"
        data = {
            "enterprise_number": enterprise_number,
            "event_type": event_type,
            "payload": payload
        }
        
        async with client.session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=3.0)) as response:
            if response.status == 200:
                logger.debug(f"✅ Sent {event_type} to RetailCRM for {enterprise_number}")
            else:
                logger.warning(f"⚠️ RetailCRM returned {response.status} for {enterprise_number}")
                
    except Exception as e:
        logger.error(f"❌ Error sending to RetailCRM for {enterprise_number}: {e}")

async def _send_to_amocrm(enterprise_number: str, event_type: str, payload: dict):
    """Отправка события в AmoCRM (заготовка)"""
    try:
        # TODO: Реализовать когда будет готов amocrm.py
        logger.debug(f"📝 Would send {event_type} to AmoCRM for {enterprise_number}")
    except Exception as e:
        logger.error(f"❌ Error sending to AmoCRM for {enterprise_number}: {e}")

# Пример использования в call-сервисах
if __name__ == "__main__":
    async def test():
        # Быстрая проверка
        enabled = await is_retailcrm_enabled("0367")
        print(f"RetailCRM enabled for 0367: {enabled}")
        
        # Отправка события
        await send_to_enabled_integrations("0367", "dial", {
            "phone": "+375296254070",
            "extension": "150"
        })
        
        # Статистика
        client = await get_client()
        stats = client.get_stats()
        print(f"Stats: {stats}")
        
        await close_client()
    
    asyncio.run(test())
