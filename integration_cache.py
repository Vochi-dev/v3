#!/usr/bin/env python3
"""
Integration Cache Service (порт 8020)

Централизованный кэш матрицы включённости интеграций для быстрого доступа 
из call-сервисов (dial, bridge, hangup, download).

Функциональность:
- In-memory кэш: enterprise_number → {retailcrm: bool, amocrm: bool, ...}
- Автоматический refresh каждые 180-300 сек
- LISTEN/NOTIFY для мгновенной инвалидации
- API для проверки статуса интеграций
- Метрики hit/miss, latency
"""

import asyncio
import asyncpg
import json
import logging
import os
import time
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, Set, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/integration_cache.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
REFRESH_INTERVAL_BASE = 240  # 4 минуты
REFRESH_JITTER_MAX = 60      # ±60 сек джиттер
TTL_SECONDS = 90             # TTL записи в кэше
CACHE_CLEANUP_INTERVAL = 30  # Очистка просроченных записей

# FastAPI приложение
app = FastAPI(title="Integration Cache Service", version="1.0.0")

# Глобальные переменные
pg_pool: Optional[asyncpg.Pool] = None
integration_cache: Dict[str, Dict[str, Any]] = {}
cache_stats = {
    "hits": 0,
    "misses": 0,
    "refreshes": 0,
    "cache_size": 0,
    "last_full_refresh": None,
    "total_requests": 0
}

class CacheEntry:
    def __init__(self, data: Dict[str, bool]):
        self.data = data
        self.created_at = time.time()
        self.expires_at = time.time() + TTL_SECONDS
    
    def is_expired(self) -> bool:
        return time.time() > self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "integrations": self.data,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "age_seconds": time.time() - self.created_at
        }

async def init_database():
    """Инициализация подключения к БД"""
    global pg_pool
    try:
        password = os.environ.get('DB_PASSWORD', 'r/Yskqh/ZbZuvjb2b3ahfg==')
        pg_pool = await asyncpg.create_pool(
            host='localhost',
            port=5432,
            user='postgres',
            password=password,
            database='postgres',
            min_size=2,
            max_size=10,
            timeout=5
        )
        logger.info("✅ Database connection pool created")
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        raise

async def load_integration_matrix() -> Dict[str, Dict[str, bool]]:
    """Загружает матрицу включённости всех интеграций из БД"""
    if not pg_pool:
        return {}
    
    try:
        async with pg_pool.acquire() as conn:
            query = """
            SELECT number, integrations_config 
            FROM enterprises 
            WHERE active = true AND integrations_config IS NOT NULL
            """
            rows = await conn.fetch(query)
            
            matrix = {}
            for row in rows:
                enterprise_number = row['number']
                integrations_config = row['integrations_config']
                
                logger.info(f"📋 Processing enterprise {enterprise_number}, config type: {type(integrations_config)}")
                
                # Парсим включённые интеграции
                enabled_integrations = {}
                if integrations_config:
                    try:
                        # Если это строка, парсим JSON
                        if isinstance(integrations_config, str):
                            integrations_config = json.loads(integrations_config)
                        
                        # integrations_config должен быть dict
                        if isinstance(integrations_config, dict):
                            for integration_type, config in integrations_config.items():
                                if isinstance(config, dict):
                                    enabled_integrations[integration_type] = config.get('enabled', False)
                                    logger.info(f"   📍 {integration_type}: enabled={config.get('enabled', False)}")
                        else:
                            logger.warning(f"⚠️ Unexpected config type for {enterprise_number}: {type(integrations_config)}")
                    except Exception as e:
                        logger.error(f"❌ Error parsing config for {enterprise_number}: {e}")
                
                matrix[enterprise_number] = enabled_integrations
            
            logger.info(f"📊 Loaded integration matrix for {len(matrix)} enterprises")
            return matrix
            
    except Exception as e:
        logger.error(f"❌ Error loading integration matrix: {e}")
        return {}

async def refresh_cache():
    """Полное обновление кэша"""
    global integration_cache, cache_stats
    
    start_time = time.time()
    matrix = await load_integration_matrix()
    
    # Атомарное обновление кэша
    new_cache = {}
    for enterprise_number, integrations in matrix.items():
        new_cache[enterprise_number] = CacheEntry(integrations)
    
    integration_cache = new_cache
    cache_stats["refreshes"] += 1
    cache_stats["cache_size"] = len(integration_cache)
    cache_stats["last_full_refresh"] = datetime.now().isoformat()
    
    elapsed = time.time() - start_time
    logger.info(f"🔄 Cache refreshed: {len(integration_cache)} entries in {elapsed:.2f}s")

async def invalidate_enterprise_cache(enterprise_number: str):
    """Инвалидация кэша для конкретного предприятия"""
    if enterprise_number in integration_cache:
        del integration_cache[enterprise_number]
        logger.info(f"🗑️ Cache invalidated for enterprise {enterprise_number}")

async def cleanup_expired_entries():
    """Очистка просроченных записей"""
    global integration_cache, cache_stats
    
    expired_keys = [
        key for key, entry in integration_cache.items() 
        if entry.is_expired()
    ]
    
    for key in expired_keys:
        del integration_cache[key]
    
    if expired_keys:
        cache_stats["cache_size"] = len(integration_cache)
        logger.info(f"🧹 Cleaned up {len(expired_keys)} expired cache entries")

async def listen_for_invalidations():
    """Слушает NOTIFY сообщения для инвалидации кэша"""
    if not pg_pool:
        return
    
    try:
        async with pg_pool.acquire() as conn:
            await conn.add_listener('integration_config_changed', 
                                  lambda conn, pid, channel, payload: 
                                  asyncio.create_task(handle_invalidation_notification(payload)))
            
            logger.info("👂 Listening for cache invalidation notifications")
            
            # Выполняем LISTEN
            await conn.execute("LISTEN integration_config_changed")
            
            # Поддерживаем соединение активным
            while True:
                await asyncio.sleep(10)
                
    except Exception as e:
        logger.error(f"❌ Error in invalidation listener: {e}")

async def handle_invalidation_notification(payload: str):
    """Обработка уведомления об изменении конфигурации"""
    try:
        data = json.loads(payload)
        enterprise_number = data.get('enterprise_number')
        if enterprise_number:
            await invalidate_enterprise_cache(enterprise_number)
    except Exception as e:
        logger.error(f"❌ Error handling invalidation notification: {e}")

# API Endpoints

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "cache_size": len(integration_cache),
        "database_connected": pg_pool is not None,
        "uptime_seconds": time.time() - start_time if 'start_time' in globals() else 0
    }

@app.get("/stats")
async def get_stats():
    """Статистика кэша"""
    hit_rate = cache_stats["hits"] / max(1, cache_stats["total_requests"]) * 100
    
    return {
        **cache_stats,
        "hit_rate_percent": round(hit_rate, 2),
        "cache_entries": len(integration_cache)
    }

@app.get("/integrations/{enterprise_number}")
async def get_integrations(enterprise_number: str):
    """Получить статус интеграций для предприятия"""
    global cache_stats
    
    cache_stats["total_requests"] += 1
    
    # Проверяем кэш
    if enterprise_number in integration_cache:
        entry = integration_cache[enterprise_number]
        if not entry.is_expired():
            cache_stats["hits"] += 1
            return entry.to_dict()
        else:
            # Просроченная запись
            del integration_cache[enterprise_number]
    
    # Cache miss - загружаем из БД
    cache_stats["misses"] += 1
    matrix = await load_integration_matrix()
    
    if enterprise_number in matrix:
        entry = CacheEntry(matrix[enterprise_number])
        integration_cache[enterprise_number] = entry
        cache_stats["cache_size"] = len(integration_cache)
        return entry.to_dict()
    
    raise HTTPException(status_code=404, detail="Enterprise not found")

@app.post("/cache/invalidate/{enterprise_number}")
async def invalidate_cache(enterprise_number: str):
    """Принудительная инвалидация кэша для предприятия"""
    await invalidate_enterprise_cache(enterprise_number)
    return {"message": f"Cache invalidated for enterprise {enterprise_number}"}

@app.post("/cache/refresh")
async def force_refresh():
    """Принудительное обновление всего кэша"""
    await refresh_cache()
    return {"message": "Cache refreshed successfully"}

@app.get("/cache/entries")
async def get_cache_entries():
    """Получить все записи кэша (для отладки)"""
    return {
        enterprise: entry.to_dict() 
        for enterprise, entry in integration_cache.items()
    }

# Background tasks

async def background_refresh_task():
    """Фоновая задача периодического обновления кэша"""
    while True:
        try:
            # Джиттер для избежания stampede
            jitter = random.randint(-REFRESH_JITTER_MAX, REFRESH_JITTER_MAX)
            sleep_time = REFRESH_INTERVAL_BASE + jitter
            
            await asyncio.sleep(sleep_time)
            await refresh_cache()
            
        except Exception as e:
            logger.error(f"❌ Error in background refresh: {e}")
            await asyncio.sleep(60)  # Retry через минуту

async def background_cleanup_task():
    """Фоновая задача очистки просроченных записей"""
    while True:
        try:
            await asyncio.sleep(CACHE_CLEANUP_INTERVAL)
            await cleanup_expired_entries()
        except Exception as e:
            logger.error(f"❌ Error in background cleanup: {e}")

# Startup event
@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    global start_time
    start_time = time.time()
    
    logger.info("🚀 Starting Integration Cache Service")
    
    # Инициализация БД
    await init_database()
    
    # Первоначальная загрузка кэша
    await refresh_cache()
    
    # Запуск фоновых задач
    asyncio.create_task(background_refresh_task())
    asyncio.create_task(background_cleanup_task())
    asyncio.create_task(listen_for_invalidations())
    
    logger.info("✅ Integration Cache Service started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Очистка при завершении"""
    if pg_pool:
        await pg_pool.close()
    logger.info("👋 Integration Cache Service stopped")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8020)
