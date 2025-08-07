"""
Кэш конфигураций интеграций для быстрого доступа
"""
import asyncio
import json
import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class IntegrationsCache:
    """Кэш конфигураций интеграций предприятий"""
    
    def __init__(self):
        self._cache: Dict[str, dict] = {}  # {enterprise_number: active_integrations}
        self._last_updated: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._is_initialized = False
    
    async def get_config(self, enterprise_number: str) -> Optional[dict]:
        """
        Получить конфигурацию интеграций для предприятия
        
        Args:
            enterprise_number: Номер предприятия (например, "0367")
            
        Returns:
            dict: Словарь активных интеграций или None если нет
            
        Example:
            {
                "retailcrm": {
                    "enabled": True,
                    "api_url": "https://test.retailcrm.ru",
                    "api_key": "key123"
                }
            }
        """
        async with self._lock:
            config = self._cache.get(enterprise_number)
            if config:
                logger.debug(f"Кэш HIT для {enterprise_number}: {list(config.keys())}")
            else:
                logger.debug(f"Кэш MISS для {enterprise_number}")
            return config
    
    async def update_config(self, enterprise_number: str, config: dict):
        """
        Обновить конфигурацию в кэше
        
        Args:
            enterprise_number: Номер предприятия
            config: Словарь активных интеграций
        """
        async with self._lock:
            self._cache[enterprise_number] = config
            self._last_updated[enterprise_number] = datetime.utcnow()
            logger.info(f"✅ Обновлен кэш интеграций для {enterprise_number}: {list(config.keys())}")
    
    async def remove_config(self, enterprise_number: str):
        """Удалить конфигурацию из кэша"""
        async with self._lock:
            self._cache.pop(enterprise_number, None)
            self._last_updated.pop(enterprise_number, None)
            logger.info(f"🗑️ Удалена конфигурация из кэша для {enterprise_number}")
    
    async def load_all_configs(self):
        """Загрузить все конфигурации из БД в кэш"""
        try:
            # Импортируем здесь чтобы избежать циклических импортов
            import asyncpg
            from app.config import DB_CONFIG
            
            conn = await asyncpg.connect(**DB_CONFIG)
            try:
                rows = await conn.fetch("""
                    SELECT number, integrations_config 
                    FROM enterprises 
                    WHERE integrations_config IS NOT NULL
                """)
                
                loaded_count = 0
                async with self._lock:
                    # Очищаем старый кэш
                    self._cache.clear()
                    self._last_updated.clear()
                    
                    for row in rows:
                        enterprise_number = row['number']
                        config_json = row['integrations_config']
                        
                        if not config_json:
                            continue
                            
                        # Парсим JSON конфигурацию
                        try:
                            full_config = json.loads(config_json) if isinstance(config_json, str) else config_json
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.error(f"Ошибка парсинга конфигурации для {enterprise_number}: {e}")
                            continue
                        
                        # Фильтруем только активные интеграции
                        active_config = {}
                        for integration_type, integration_config in full_config.items():
                            if isinstance(integration_config, dict) and integration_config.get('enabled', False):
                                active_config[integration_type] = integration_config
                        
                        # Сохраняем в кэш только если есть активные интеграции
                        if active_config:
                            self._cache[enterprise_number] = active_config
                            self._last_updated[enterprise_number] = datetime.utcnow()
                            loaded_count += 1
                
                self._is_initialized = True
                logger.info(f"🚀 Загружено {loaded_count} конфигураций интеграций в кэш")
                
                # Логируем детали для отладки
                for ent_num, config in self._cache.items():
                    integrations_list = list(config.keys())
                    logger.info(f"  📋 {ent_num}: {integrations_list}")
                    
            finally:
                await conn.close()
                
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки конфигураций в кэш: {e}")
            self._is_initialized = False
    
    async def has_active_integrations(self, enterprise_number: str) -> bool:
        """Быстрая проверка наличия активных интеграций"""
        async with self._lock:
            return enterprise_number in self._cache
    
    async def get_integration_types(self, enterprise_number: str) -> list:
        """Получить список типов активных интеграций"""
        config = await self.get_config(enterprise_number)
        return list(config.keys()) if config else []
    
    def get_cache_stats(self) -> dict:
        """Статистика кэша для мониторинга"""
        cache_size_bytes = len(str(self._cache).encode('utf-8'))
        
        return {
            "cached_enterprises": len(self._cache),
            "total_integrations": sum(len(config) for config in self._cache.values()),
            "last_update": max(self._last_updated.values()) if self._last_updated else None,
            "cache_size_kb": round(cache_size_bytes / 1024, 2),
            "is_initialized": self._is_initialized,
            "enterprises": {
                ent_num: list(config.keys()) 
                for ent_num, config in self._cache.items()
            }
        }
    
    async def clear_cache(self):
        """Очистить весь кэш"""
        async with self._lock:
            self._cache.clear()
            self._last_updated.clear()
            self._is_initialized = False
            logger.warning("🧹 Кэш интеграций очищен")


# Глобальный экземпляр кэша
integrations_cache = IntegrationsCache()