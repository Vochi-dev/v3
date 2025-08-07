"""
Менеджер автоматического обновления кэша интеграций
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from .cache import integrations_cache

logger = logging.getLogger(__name__)


class CacheManager:
    """Менеджер обновления кэша интеграций"""
    
    def __init__(self, update_interval: int = 300):  # 5 минут по умолчанию
        self.cache = integrations_cache
        self.update_task: Optional[asyncio.Task] = None
        self.update_interval = update_interval
        self._is_running = False
        
    async def start_cache_updater(self):
        """Запустить фоновое обновление кэша"""
        if not self.update_task or self.update_task.done():
            logger.info(f"🔄 Запуск автообновления кэша интеграций (интервал: {self.update_interval}s)")
            self.update_task = asyncio.create_task(self._cache_update_loop())
            self._is_running = True
    
    async def stop_cache_updater(self):
        """Остановить фоновое обновление"""
        if self.update_task and not self.update_task.done():
            logger.info("⏹️ Остановка автообновления кэша интеграций")
            self.update_task.cancel()
            try:
                await self.update_task
            except asyncio.CancelledError:
                pass
            self.update_task = None
            self._is_running = False
    
    async def _cache_update_loop(self):
        """Фоновый цикл обновления кэша"""
        # Первоначальная загрузка
        await self.cache.load_all_configs()
        
        while True:
            try:
                await asyncio.sleep(self.update_interval)
                
                logger.debug("🔄 Автообновление кэша интеграций...")
                start_time = datetime.utcnow()
                
                await self.cache.load_all_configs()
                
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                logger.debug(f"✅ Кэш обновлен за {elapsed:.2f}s")
                
            except asyncio.CancelledError:
                logger.info("🛑 Цикл обновления кэша отменен")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка автообновления кэша интеграций: {e}")
                # Не прерываем цикл при ошибках, ждем и пробуем снова
                await asyncio.sleep(30)  # Короткая пауза при ошибке
    
    async def force_reload_enterprise(self, enterprise_number: str):
        """
        Принудительно перезагрузить конфигурацию одного предприятия
        
        Args:
            enterprise_number: Номер предприятия для обновления
        """
        try:
            import asyncpg
            from app.config import DB_CONFIG
            
            logger.info(f"🔄 Принудительное обновление кэша для {enterprise_number}")
            
            conn = await asyncpg.connect(**DB_CONFIG)
            try:
                row = await conn.fetchrow("""
                    SELECT integrations_config 
                    FROM enterprises 
                    WHERE number = $1
                """, enterprise_number)
                
                if row and row['integrations_config']:
                    # Парсим конфигурацию
                    config_json = row['integrations_config']
                    try:
                        full_config = json.loads(config_json) if isinstance(config_json, str) else config_json
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.error(f"Ошибка парсинга конфигурации для {enterprise_number}: {e}")
                        await self.cache.remove_config(enterprise_number)
                        return
                    
                    # Фильтруем активные интеграции
                    active_config = {}
                    for integration_type, integration_config in full_config.items():
                        if isinstance(integration_config, dict) and integration_config.get('enabled', False):
                            active_config[integration_type] = integration_config
                    
                    # Обновляем кэш
                    if active_config:
                        await self.cache.update_config(enterprise_number, active_config)
                        logger.info(f"✅ Кэш обновлен для {enterprise_number}: {list(active_config.keys())}")
                    else:
                        await self.cache.remove_config(enterprise_number)
                        logger.info(f"🗑️ Нет активных интеграций для {enterprise_number}, удалено из кэша")
                else:
                    # Нет конфигурации в БД - удаляем из кэша
                    await self.cache.remove_config(enterprise_number)
                    logger.info(f"🗑️ Конфигурация отсутствует в БД для {enterprise_number}, удалено из кэша")
                    
            finally:
                await conn.close()
                
        except Exception as e:
            logger.error(f"❌ Ошибка принудительного обновления кэша для {enterprise_number}: {e}")
    
    async def warm_up_cache(self):
        """
        Предварительный прогрев кэша при старте приложения
        """
        logger.info("🔥 Прогрев кэша интеграций...")
        start_time = datetime.utcnow()
        
        await self.cache.load_all_configs()
        
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        stats = self.cache.get_cache_stats()
        
        logger.info(f"✅ Кэш прогрет за {elapsed:.2f}s: {stats['cached_enterprises']} предприятий, {stats['total_integrations']} интеграций")
    
    def get_manager_stats(self) -> dict:
        """Статистика менеджера кэша"""
        return {
            "is_running": self._is_running,
            "update_interval": self.update_interval,
            "task_status": "running" if self.update_task and not self.update_task.done() else "stopped",
            "cache_stats": self.cache.get_cache_stats()
        }
    
    async def health_check(self) -> dict:
        """Проверка состояния системы кэширования"""
        stats = self.get_manager_stats()
        cache_stats = stats["cache_stats"]
        
        # Проверяем актуальность кэша
        is_fresh = True
        if cache_stats["last_update"]:
            age = datetime.utcnow() - cache_stats["last_update"]
            is_fresh = age < timedelta(minutes=10)  # Кэш считается свежим 10 минут
        
        health = {
            "status": "healthy" if stats["is_running"] and is_fresh else "degraded",
            "manager_running": stats["is_running"],
            "cache_initialized": cache_stats["is_initialized"],
            "cache_fresh": is_fresh,
            "cached_enterprises": cache_stats["cached_enterprises"],
            "total_integrations": cache_stats["total_integrations"],
            "last_update": cache_stats["last_update"]
        }
        
        return health


# Глобальный менеджер кэша
cache_manager = CacheManager()