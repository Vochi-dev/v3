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
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import JSONResponse
import httpx

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
# Новый кэш для полных конфигураций
full_config_cache: Dict[str, Dict[str, Any]] = {}
cache_stats = {
    "hits": 0,
    "misses": 0,
    "refreshes": 0,
    "cache_size": 0,
    "config_hits": 0,
    "config_misses": 0,
    "config_cache_size": 0,
    "last_full_refresh": None,
    "total_requests": 0
}

# Cache for customer names: key=(enterprise, phone_e164) -> {"name": str|None, "expires": epoch}
customer_name_cache: Dict[str, Dict[str, Any]] = {}
# Cache for customer profiles: key=(enterprise, phone_e164) -> {"last_name":str|None, "first_name":str|None, "middle_name":str|None, "enterprise_name":str|None, "expires": epoch}
customer_profile_cache: Dict[str, Dict[str, Any]] = {}
# Cache for responsible extensions: key=(enterprise, phone_e164) -> {"ext": str|None, "expires": epoch}
responsible_ext_cache: Dict[str, Dict[str, Any]] = {}
# Cache for incoming transforms per enterprise: enterprise -> {"map": {"sip:<line>": "+375{9}", ...}, "expires": epoch}
incoming_transform_cache: Dict[str, Dict[str, Any]] = {}

# Недавние dial-события для идемпотентности и синтетического поднятия карточки при пропусках
recent_dials: Dict[str, float] = {}
RECENT_DIAL_TTL = 300  # 5 минут

# Дедупликация hangup событий по unique_id
processed_hangups: Dict[str, float] = {}
processed_dials: Dict[str, float] = {}
HANGUP_DEDUP_TTL = 300  # 5 минут
DIAL_DEDUP_TTL = 60     # 1 минута

# ===== Вспомогательные функции =====

async def fetch_enterprise_by_token(token: str) -> Optional[str]:
    """Возвращает enterprise_number по токену (name2 или secret)."""
    logger.info(f"🔍 fetch_enterprise_by_token called with token='{token}'")
    if not pg_pool:
        logger.error("❌ pg_pool is None")
        return None
    try:
        async with pg_pool.acquire() as conn:
            # Принимаем в качестве токена: name2, secret ИЛИ сам номер предприятия
            row = await conn.fetchrow(
                """
                SELECT number FROM enterprises
                WHERE name2 = $1 OR secret = $1 OR number = $1
                LIMIT 1
                """,
                str(token),
            )
            if row:
                result = row["number"]
                logger.info(f"✅ Found enterprise {result} for token '{token}'")
                return result
            else:
                logger.error(f"❌ NO enterprise found for token '{token}'")
                return None
    except Exception as e:
        logger.error(f"❌ fetch_enterprise_by_token error: {e}")
        return None

async def fetch_retailcrm_config(enterprise_number: str) -> Optional[dict]:
    """Читает integrations_config->retailcrm для юнита."""
    if not pg_pool:
        return None
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT integrations_config -> 'retailcrm' AS cfg
                FROM enterprises WHERE number = $1
                """,
                enterprise_number,
            )
            if not row or not row["cfg"]:
                return None
            cfg = row["cfg"]
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            return cfg
    except Exception as e:
        logger.error(f"❌ fetch_retailcrm_config error: {e}")
        return None

async def write_integration_log(
    enterprise_number: str,
    event_type: str,
    request_data: Dict[str, Any],
    response_data: Optional[Dict[str, Any]],
    status_ok: bool,
    error_message: Optional[str] = None,
    integration_type: str = "uon",
) -> None:
    """Запись события интеграции (новая схема с фолбэком на старую).

    Совместимо с функцией из retailcrm.py.
    """
    try:
        if not pg_pool:
            await init_database()
        if not pg_pool:
            return
        status_str = "success" if status_ok else "error"
        try:
            sql_new = (
                "INSERT INTO integration_logs(enterprise_number, integration_type, event_type, request_data, response_data, status, error_message) "
                "VALUES($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)"
            )
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    sql_new,
                    enterprise_number,
                    integration_type,
                    event_type,
                    json.dumps(request_data),
                    json.dumps(response_data or {}),
                    status_str,
                    error_message,
                )
        except Exception as e_new:
            try:
                sql_old = (
                    "INSERT INTO integration_logs(enterprise_number, integration_type, action, payload, response, success, error) "
                    "VALUES($1, $2, $3, $4::jsonb, $5::jsonb, $6::boolean, $7)"
                )
                async with pg_pool.acquire() as conn:
                    await conn.execute(
                        sql_old,
                        enterprise_number,
                        integration_type,
                        event_type,
                        json.dumps(request_data),
                        json.dumps(response_data or {}),
                        status_ok,
                        error_message,
                    )
            except Exception as e_old:
                logger.warning(f"write_integration_log failed (new='{e_new}', old='{e_old}')")
    except Exception as e:
        logger.warning(f"write_integration_log outer failed: {e}")

def map_call_type(call_type: int) -> Optional[str]:
    if call_type == 0:
        return "in"
    if call_type == 1:
        return "out"
    return None

def normalize_phone_e164(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("+"):
        return raw
    # упрощённая нормализация
    if raw.isdigit():
        return "+" + raw
    return raw

def phone_without_plus(e164: str) -> str:
    return e164[1:] if e164.startswith("+") else e164

def determine_internal_code(raw_event: dict) -> Optional[str]:
    """Определяем внутренний добавочный сотрудника. Приоритет: CallerIDNum → Extensions → ConnectedLineNum.
    Это важно для исходящих вызовов, где CallerIDNum — внутренний звонящего, а Extensions может содержать внешний номер."""
    caller = str(raw_event.get("CallerIDNum", ""))
    if caller and caller.isdigit() and 2 <= len(caller) <= 5:
        return str(caller)
    exts = raw_event.get("Extensions", []) or []
    for ext in exts:
        if ext and str(ext).isdigit() and 2 <= len(str(ext)) <= 5:
            return str(ext)
    connected = str(raw_event.get("ConnectedLineNum", ""))
    if connected and connected.isdigit() and 2 <= len(connected) <= 5:
        return str(connected)
    return None

def map_result(call_status: int) -> str:
    return "answered" if call_status == 2 else "missed"

async def post_retailcrm_call_event(base_url: str, api_key: str, client_id: str, event: dict) -> dict:
    import aiohttp
    url = f"{base_url}/api/v5/telephony/call/event?apiKey={api_key}"
    data = {
        "clientId": client_id,
        "event": json.dumps(event)
    }
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=data) as resp:
            try:
                return await resp.json()
            except Exception:
                return {"success": False, "status": resp.status}

async def post_retailcrm_calls_upload(base_url: str, api_key: str, client_id: str, calls: list[dict]) -> dict:
    import aiohttp
    url = f"{base_url}/api/v5/telephony/calls/upload?apiKey={api_key}"
    data = {
        "clientId": client_id,
        "calls": json.dumps(calls)
    }
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=data) as resp:
            try:
                return await resp.json()
            except Exception:
                return {"success": False, "status": resp.status}

def _is_internal(num: str) -> bool:
    try:
        return bool(num) and str(num).isdigit() and 2 <= len(str(num)) <= 5
    except Exception:
        return False

def _collect_internal_exts(raw: dict) -> list[str]:
    exts = []
    for ext in (raw.get("Extensions") or []):
        if _is_internal(ext):
            exts.append(str(ext))
    return exts

def guess_direction_and_phone(raw: dict, fallback: Optional[str]) -> tuple[str, str]:
    """Возвращает (event_kind, external_phone_e164).
    Правила:
    - Если CallType корректен — используем его.
    - Исходящий: CallerIDNum — внутренний, среди Extensions есть внешний или Phone внешний.
    - Входящий: CallerIDNum — внешний, среди Extensions есть внутренний.
    - Прочие случаи: эвристика по соотношению внутренних/внешних.
    """
    ct = raw.get("CallType")
    if isinstance(ct, (int, str)) and str(ct).isdigit():
        kind = map_call_type(int(ct))
        if kind in {"in", "out"}:
            phone = raw.get("Phone") or raw.get("CallerIDNum") or raw.get("ConnectedLineNum") or fallback or ""
            return kind, normalize_phone_e164(phone)

    caller = str(raw.get("CallerIDNum") or "")
    phone_field = str(raw.get("Phone") or "")
    connected = str(raw.get("ConnectedLineNum") or "")
    exts = list(raw.get("Extensions") or [])

    caller_internal = _is_internal(caller)
    any_internal_ext = any(_is_internal(e) for e in exts)
    any_external_ext = any((not _is_internal(e)) and e for e in exts)
    phone_is_external = bool(phone_field and not _is_internal(phone_field))

    # Явный исходящий: внутренний звонящий + есть внешний номер среди Extensions или в Phone
    if caller_internal and (any_external_ext or phone_is_external):
        external = next((e for e in exts if e and not _is_internal(e)), None) or phone_field or connected
        return "out", normalize_phone_e164(external)

    # Явный входящий: внешний CallerIDNum и есть внутренние Extensions
    if (not caller_internal) and any_internal_ext:
        external = caller or phone_field or connected
        return "in", normalize_phone_e164(external)

    # Неопределённые сочетания — мягкая эвристика
    if caller_internal and not any_internal_ext:
        external = phone_field or connected
        return "out", normalize_phone_e164(external)
    if (not caller_internal) and any_internal_ext:
        external = caller or phone_field or connected
        return "in", normalize_phone_e164(external)

    # fallback: считаем входящим
    return ("in", normalize_phone_e164(fallback or phone_field or caller or connected))

def pick_internal_code_for_hangup(raw: dict) -> Optional[str]:
    """Более агрессивный выбор внутреннего кода для события hangup (recovery).
    Порядок: CallerIDNum → любой из Extensions → ConnectedLineNum → InternalCode.
    """
    cand = str(raw.get("CallerIDNum") or "")
    if _is_internal(cand):
        return cand
    for e in (raw.get("Extensions") or []):
        if _is_internal(str(e)):
            return str(e)
    connected = str(raw.get("ConnectedLineNum") or "")
    if _is_internal(connected):
        return connected
    ic = str(raw.get("InternalCode") or "")
    if _is_internal(ic):
        return ic
    return None

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
    """Загружает матрицу включённости всех интеграций из БД (для совместимости)"""
    full_configs = await load_full_integration_configs()
    
    # Преобразуем полные конфигурации в матрицу enabled
    matrix = {}
    for enterprise_number, configs in full_configs.items():
        enabled_integrations = {}
        for integration_type, config in configs.items():
            if isinstance(config, dict):
                enabled_integrations[integration_type] = config.get('enabled', False)
        matrix[enterprise_number] = enabled_integrations
    
    return matrix

async def load_full_integration_configs() -> Dict[str, Dict[str, Any]]:
    """Загружает ПОЛНЫЕ конфигурации всех интеграций из БД"""
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
            
            configs = {}
            for row in rows:
                enterprise_number = row['number']
                integrations_config = row['integrations_config']
                
                logger.info(f"📋 Processing enterprise {enterprise_number}, config type: {type(integrations_config)}")
                
                # Парсим полные конфигурации
                full_configs = {}
                if integrations_config:
                    try:
                        # Если это строка, парсим JSON
                        if isinstance(integrations_config, str):
                            integrations_config = json.loads(integrations_config)
                        
                        # integrations_config должен быть dict
                        if isinstance(integrations_config, dict):
                            # Сохраняем ПОЛНЫЕ конфигурации
                            for integration_type, config in integrations_config.items():
                                if isinstance(config, dict):
                                    full_configs[integration_type] = config
                                    logger.info(f"   📍 {integration_type}: enabled={config.get('enabled', False)}")
                        else:
                            logger.warning(f"⚠️ Unexpected config type for {enterprise_number}: {type(integrations_config)}")
                    except Exception as e:
                        logger.error(f"❌ Error parsing config for {enterprise_number}: {e}")
                
                configs[enterprise_number] = full_configs
            
        logger.info(f"📊 Loaded full integration configs for {len(configs)} enterprises")
        return configs
        
    except Exception as e:
        logger.error(f"❌ Error loading integration matrix: {e}")
        return {}

async def load_enterprise_full_config(enterprise_number: str) -> Optional[Dict[str, Any]]:
    """Загружает ПОЛНУЮ конфигурацию интеграций для конкретного предприятия и обновляет кэш"""
    global full_config_cache
    
    if not pg_pool:
        return None
    
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1 AND active = true", 
                enterprise_number
            )
            
            if not row:
                logger.warning(f"⚠️ Enterprise {enterprise_number} not found or inactive")
                return None
            
            integrations_config = row['integrations_config']
            
            # Парсим полные конфигурации
            full_configs = {}
            if integrations_config:
                try:
                    # Если это строка, парсим JSON
                    if isinstance(integrations_config, str):
                        integrations_config = json.loads(integrations_config)
                    
                    # integrations_config должен быть dict
                    if isinstance(integrations_config, dict):
                        # Сохраняем ПОЛНЫЕ конфигурации
                        for integration_type, config in integrations_config.items():
                            if isinstance(config, dict):
                                full_configs[integration_type] = config
                                logger.info(f"   📍 {integration_type}: enabled={config.get('enabled', False)}")
                    else:
                        logger.warning(f"⚠️ Unexpected config type for {enterprise_number}: {type(integrations_config)}")
                except Exception as e:
                    logger.error(f"❌ Error parsing config for {enterprise_number}: {e}")
            
            # Обновляем кэш полных конфигураций
            full_config_cache[enterprise_number] = CacheEntry({"integrations": full_configs})
            
            logger.info(f"🔄 Refreshed full config cache for enterprise {enterprise_number}")
            return full_configs
            
    except Exception as e:
        logger.error(f"❌ Error loading config for enterprise {enterprise_number}: {e}")
        return None

async def refresh_cache():
    """Полное обновление кэша"""
    global integration_cache, full_config_cache, cache_stats
    
    start_time = time.time()
    
    # Загружаем полные конфигурации
    full_configs = await load_full_integration_configs()
    
    # Атомарное обновление кэша полных конфигураций
    new_full_cache = {}
    for enterprise_number, configs in full_configs.items():
        new_full_cache[enterprise_number] = CacheEntry(configs)
    
    # Преобразуем в матрицу enabled для совместимости
    matrix = {}
    for enterprise_number, configs in full_configs.items():
        enabled_integrations = {}
        for integration_type, config in configs.items():
            if isinstance(config, dict):
                enabled_integrations[integration_type] = config.get('enabled', False)
        matrix[enterprise_number] = enabled_integrations
    
    # Атомарное обновление кэша статусов
    new_cache = {}
    for enterprise_number, integrations in matrix.items():
        new_cache[enterprise_number] = CacheEntry(integrations)
    
    integration_cache = new_cache
    full_config_cache = new_full_cache
    cache_stats["refreshes"] += 1
    cache_stats["cache_size"] = len(integration_cache)
    cache_stats["config_cache_size"] = len(full_config_cache)
    cache_stats["last_full_refresh"] = datetime.now().isoformat()
    
    elapsed = time.time() - start_time
    logger.info(f"🔄 Cache refreshed: {len(integration_cache)} entries in {elapsed:.2f}s")

async def load_incoming_transform_map(enterprise_number: str) -> Dict[str, str]:
    """Загружает карту правил incoming_transform для всех SIP-линий юнита.
    Возвращает: { "sip:<line_name>": "+375{9}", ... }
    """
    if not pg_pool:
        return {}
    try:
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT line_name, incoming_transform
                FROM sip_unit
                WHERE enterprise_number = $1
                  AND incoming_transform IS NOT NULL
                  AND trim(incoming_transform) <> ''
                """,
                enterprise_number,
            )
            result: Dict[str, str] = {}
            for r in rows:
                ln = str(r["line_name"]) if r and r["line_name"] is not None else None
                tr = str(r["incoming_transform"]).strip() if r and r["incoming_transform"] is not None else ""
                if ln and tr:
                    result[f"sip:{ln}"] = tr
            return result
    except Exception as e:
        logger.error(f"❌ load_incoming_transform_map error: {e}")
        return {}

async def get_enterprise_name2(enterprise_number: str) -> Optional[str]:
    """Получить name2 для предприятия (для токенов RetailCRM)"""
    try:
        await init_database()
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT name2 FROM enterprises WHERE number = $1", enterprise_number)
            return row["name2"] if row else None
    except Exception as e:
        logger.error(f"get_enterprise_name2 error: {e}")
        return None

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
    
    config_hit_rate = cache_stats["config_hits"] / max(1, cache_stats["config_hits"] + cache_stats["config_misses"]) * 100
    
    return {
        **cache_stats,
        "hit_rate_percent": round(hit_rate, 2),
        "config_hit_rate_percent": round(config_hit_rate, 2),
        "cache_entries": len(integration_cache),
        "config_cache_entries": len(full_config_cache),
        "incoming_transform_cached": len(incoming_transform_cache)
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
    
    # ИГНОРИРУЕМ предприятия без интеграций
    logger.info(f"⚠️ Enterprise {enterprise_number} has no integrations configured - IGNORING")
    raise HTTPException(status_code=404, detail="Enterprise not found")

@app.get("/config/{enterprise_number}")
async def get_integration_config(enterprise_number: str):
    """Получить ПОЛНУЮ конфигурацию интеграций для предприятия"""
    global cache_stats, full_config_cache
    
    cache_stats["total_requests"] += 1
    
    # Проверяем кэш полных конфигураций
    if enterprise_number in full_config_cache:
        entry = full_config_cache[enterprise_number]
        if not entry.is_expired():
            cache_stats["config_hits"] += 1
            integrations_data = entry.to_dict()
            # Исправляем дублирование структуры
            if "integrations" in integrations_data:
                integrations_data = integrations_data["integrations"]
            
            return {
                "enterprise_number": enterprise_number,
                "integrations": integrations_data,
                "source": "cache",
                "cached_at": datetime.fromtimestamp(entry.created_at).isoformat() if hasattr(entry, 'created_at') else None
            }
        else:
            # Просроченная запись
            del full_config_cache[enterprise_number]
    
    # Cache miss - загружаем из БД
    cache_stats["config_misses"] += 1
    full_configs = await load_full_integration_configs()
    
    if enterprise_number in full_configs:
        configs = full_configs[enterprise_number]
        entry = CacheEntry(configs)
        full_config_cache[enterprise_number] = entry
        cache_stats["config_cache_size"] = len(full_config_cache)
        
        return {
            "enterprise_number": enterprise_number,
            "integrations": configs,
            "source": "database",
            "cached_at": datetime.fromtimestamp(entry.created_at).isoformat() if hasattr(entry, 'created_at') else None
        }
    
    # Предприятие не найдено или нет конфигураций
    raise HTTPException(
        status_code=404, 
        detail=f"No integration configurations found for enterprise {enterprise_number}"
    )

@app.get("/config/{enterprise_number}/{integration_type}")
async def get_specific_integration_config(enterprise_number: str, integration_type: str):
    """Получить конфигурацию конкретной интеграции"""
    full_config = await get_integration_config(enterprise_number)
    
    integrations = full_config.get("integrations", {})
    
    # Исправляем дублирование структуры если есть integrations.integrations
    if "integrations" in integrations:
        integrations = integrations["integrations"]
    
    if integration_type not in integrations:
        raise HTTPException(
            status_code=404,
            detail=f"Integration '{integration_type}' not configured for enterprise {enterprise_number}"
        )
    
    return {
        "enterprise_number": enterprise_number,
        "integration_type": integration_type,
        "config": integrations[integration_type],
        "source": full_config.get("source", "unknown")
    }

@app.get("/incoming-transform/{enterprise_number}")
async def get_incoming_transform(enterprise_number: str):
    """Возвращает карту правил incoming_transform для юнита (кэшируется на TTL_SECONDS)."""
    now = time.time()
    entry = incoming_transform_cache.get(enterprise_number)
    if entry and entry.get("expires", 0) > now:
        return {"map": entry.get("map", {})}

    if not pg_pool:
        await init_database()
    if not pg_pool:
        return {"map": {}}

    m = await load_incoming_transform_map(enterprise_number)
    incoming_transform_cache[enterprise_number] = {"map": m, "expires": now + TTL_SECONDS}
    return {"map": m}

@app.post("/reload-incoming-transform/{enterprise_number}")
async def reload_incoming_transform(enterprise_number: str):
    """Принудительно перезагружает карту incoming_transform для юнита."""
    if not pg_pool:
        await init_database()
    m = await load_incoming_transform_map(enterprise_number)
    incoming_transform_cache[enterprise_number] = {"map": m, "expires": time.time() + TTL_SECONDS}
    return {"size": len(m)}

@app.post("/dispatch/call-event")
async def dispatch_call_event(request: Request):
    """Принимает события от dial/hangup и отправляет в активные интеграции."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = body.get("token")
    unique_id = body.get("uniqueId")
    event_type = body.get("event_type")  # dial|hangup
    raw = body.get("raw", {}) or {}
    record_url = body.get("record_url")
    origin = body.get("origin")  # 'download' для восстановленных событий

    logger.info(f"📥 dispatch_call_event: token='{token}', event_type={event_type}, unique_id={unique_id}")
    
    if not token or not unique_id or event_type not in {"dial", "hangup"}:
        raise HTTPException(status_code=400, detail="invalid payload")
    
    # Дедупликация dial и hangup событий
    import time
    now = time.time()
    
    if event_type == "dial":
        # Очищаем старые dial записи
        expired_keys = [k for k, v in processed_dials.items() if now - v > DIAL_DEDUP_TTL]
        for k in expired_keys:
            del processed_dials[k]
        
        # Проверяем дубликат dial
        if unique_id in processed_dials:
            logger.info(f"🚫 Duplicate dial event for unique_id={unique_id} - IGNORING")
            return {"status": "ignored", "reason": "duplicate"}
        
        # Запоминаем dial
        processed_dials[unique_id] = now
    
    elif event_type == "hangup":
        # Очищаем старые hangup записи
        expired_keys = [k for k, v in processed_hangups.items() if now - v > HANGUP_DEDUP_TTL]
        for k in expired_keys:
            del processed_hangups[k]
        
        # Проверяем дубликат hangup
        if unique_id in processed_hangups:
            logger.info(f"🚫 Duplicate hangup event for unique_id={unique_id} - IGNORING")
            return {"status": "ignored", "reason": "duplicate"}
        
        # Запоминаем hangup
        processed_hangups[unique_id] = now

    enterprise_number = await fetch_enterprise_by_token(token)
    if not enterprise_number:
        logger.error(f"❌ Enterprise not found for token='{token}', event_type={event_type}, unique_id={unique_id}")
        raise HTTPException(status_code=404, detail="enterprise not found by token")

    logger.info(f"🏢 Using enterprise_number='{enterprise_number}' for token='{token}'")
    
    # Проверяем кэш активных интеграций
    integrations_entry = integration_cache.get(enterprise_number)
    if not integrations_entry or integrations_entry.is_expired():
        # подгрузим одну запись
        await get_integrations(enterprise_number)
        integrations_entry = integration_cache.get(enterprise_number)
    active = integrations_entry.data if integrations_entry else {}

    # Попробуем применить входящее преобразование номера для SIP-линий
    try:
        trunk = str(
            raw.get("TrunkId")
            or raw.get("Trunk")
            or raw.get("INCALL")
            or raw.get("Incall")
            or ""
        ).strip()
        if trunk:
            # Получаем карту правил для юнита
            mresp = await get_incoming_transform(enterprise_number)  # reuse local endpoint logic
            tmap = (mresp or {}).get("map") or {}
            rule = tmap.get(f"sip:{trunk}") or tmap.get(f"gsm:{trunk}")
            if isinstance(rule, str) and "{" in rule and "}" in rule:
                pref = rule.split("{")[0]
                try:
                    n = int(rule.split("{")[1].split("}")[0])
                except Exception:
                    n = None
                # Определим внешний номер из raw (как поступал ранее)
                _, external_e164 = guess_direction_and_phone(raw, None)
                digits = ''.join([c for c in external_e164 if c.isdigit()])
                if n and len(digits) >= n:
                    normalized = f"{pref}{digits[-n:]}"
                    # Применим нормализацию обратно в raw/Phone
                    if raw.get("Phone"):
                        raw = dict(raw)
                        raw["Phone"] = normalized
                    else:
                        raw = dict(raw)
                        raw["CallerIDNum"] = normalized
    except Exception as e:
        logger.warning(f"incoming_transform apply failed: {e}")

    # Лог приёма события
    logger.info(f"➡️ Received event: enterprise={enterprise_number} type={event_type} uniqueId={unique_id}")

    # RetailCRM → пересылка в интеграционный сервис 8019
    if active.get("retailcrm"):
        # Отмечаем dial, чтобы на hangup при необходимости сделать синтетический dial
        if event_type == "dial":
            recent_dials[unique_id] = time.time()
        try:
            import aiohttp
            # Получаем name2 для RetailCRM (токен != enterprise_number)
            retailcrm_token = await get_enterprise_name2(enterprise_number)
            payload_forward = {
                "token": retailcrm_token or token,  # fallback на enterprise_number если name2 не найден
                "uniqueId": unique_id,
                "event_type": event_type,
                "raw": raw,
                "record_url": record_url,
            }
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Если пришёл hangup и не было dial — сначала синтетический dial
                # НО: не делаем synthetic dial для событий, пришедших из download (recovery)
                if event_type == "hangup" and unique_id not in recent_dials and origin != "download":
                    synth = dict(payload_forward)
                    synth["event_type"] = "dial"
                    async with session.post("http://127.0.0.1:8019/internal/retailcrm/call-event", json=synth) as r1:
                        logger.info(f"→ 8019 synthetic dial: status={r1.status}")
                # Для hangup (в т.ч. из download) подчистим raw доп. полем InternalCode, если его нет
                if event_type == "hangup" and "InternalCode" not in raw:
                    ic = pick_internal_code_for_hangup(raw)
                    if ic:
                        payload_forward = dict(payload_forward)
                        payload_forward["raw"] = dict(payload_forward["raw"])  # copy
                        payload_forward["raw"]["InternalCode"] = ic

                async with session.post("http://127.0.0.1:8019/internal/retailcrm/call-event", json=payload_forward) as r2:
                    logger.info(f"→ 8019 forward {event_type}: status={r2.status}")
        except Exception as e:
            logger.error(f"❌ Forward to 8019 failed: {e}")

    # U-ON → всплывашка (dial) и запись истории (hangup)
    if active.get("uon"):
        try:
            import aiohttp
            # Определяем направление/номер/внутренний
            try:
                event_kind, external_phone_e164 = guess_direction_and_phone(raw, None)
            except Exception:
                event_kind, external_phone_e164 = ("in", normalize_phone_e164(str(raw.get("Phone") or "")))
            internal_code = determine_internal_code(raw) if event_type == "dial" else pick_internal_code_for_hangup(raw)

            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if event_type == "dial":
                    # Для dial предпочитаем внутренний код из Extensions, а не CallerIDNum
                    preferred_ext = None
                    try:
                        for e in (raw.get("Extensions") or []):
                            if e and str(e).isdigit() and 2 <= len(str(e)) <= 5:
                                preferred_ext = str(e)
                                break
                    except Exception:
                        preferred_ext = None
                    final_ext = preferred_ext or internal_code or ""
                    # антидубль на стороне 8020: пометим этот dial, чтобы hangup не слал повторно
                    recent_dials[unique_id] = time.time()
                    # Собираем все кандидаты добавочных из raw.Extensions (только внутренние коды)
                    ext_candidates: list[str] = []
                    try:
                        for e in (raw.get("Extensions") or []):
                            es = str(e)
                            if es.isdigit() and 2 <= len(es) <= 5 and es not in ext_candidates:
                                ext_candidates.append(es)
                    except Exception:
                        pass
                    if final_ext and final_ext not in ext_candidates:
                        ext_candidates.insert(0, final_ext)
                    payload = {
                        "enterprise_number": enterprise_number,
                        "phone": external_phone_e164,
                        "extension": final_ext,
                        "extensions_all": ext_candidates,
                        "direction": event_kind,  # Добавляем direction!
                    }
                    try:
                        async with session.post("http://127.0.0.1:8022/internal/uon/notify-incoming", json=payload) as r:
                            ok = (r.status == 200)
                            try:
                                data = await r.json()
                            except Exception:
                                data = {"status": r.status}
                            await write_integration_log(
                                enterprise_number=enterprise_number,
                                event_type=f"call_event:{event_type}",
                                request_data={"uniqueId": unique_id, "payload": payload},
                                response_data=data,
                                status_ok=ok,
                                error_message=None if ok else f"http={r.status}",
                                integration_type="uon",
                            )
                    except Exception as er:
                        await write_integration_log(
                            enterprise_number=enterprise_number,
                            event_type=f"call_event:{event_type}",
                            request_data={"uniqueId": unique_id, "payload": payload},
                            response_data=None,
                            status_ok=False,
                            error_message=str(er),
                            integration_type="uon",
                        )
                elif event_type == "hangup":
                    # Используем ту же логику что и RetailCRM для вычисления длительности
                    from datetime import datetime
                    start_time = raw.get("StartTime") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    end_time = raw.get("EndTime") or start_time
                    duration = 0
                    try:
                        dt_s = datetime.fromisoformat(start_time.replace("Z", ""))
                        dt_e = datetime.fromisoformat(end_time.replace("Z", ""))
                        duration = max(0, int((dt_e - dt_s).total_seconds()))
                    except Exception:
                        duration = 0
                    
                    # Определяем статус звонка на основе CallStatus
                    call_status = int(raw.get("CallStatus", 0))
                    status_text = "отвеченный" if call_status == 2 else "неотвеченный"
                    start_ts = str(
                        raw.get("StartTime")
                        or raw.get("start")
                        or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    direction = "in" if event_kind == "in" else "out"
                    payload = {
                        "enterprise_number": enterprise_number,
                        "phone": external_phone_e164,
                        "extension": internal_code or "",
                        "start": start_ts,
                        "duration": duration,
                        "direction": direction,
                        "call_status": status_text
                    }
                    if record_url:
                        payload["record_url"] = record_url
                    logger.info(f"🔗 Sending to UON log-call: {payload}")
                    try:
                        async with session.post("http://127.0.0.1:8022/internal/uon/log-call", json=payload) as r:
                            ok = (r.status == 200)
                            try:
                                data = await r.json()
                            except Exception:
                                data = {"status": r.status}
                            await write_integration_log(
                                enterprise_number=enterprise_number,
                                event_type="call_history",
                                request_data={"uniqueId": unique_id, "payload": payload},
                                response_data=data,
                                status_ok=ok,
                                error_message=None if ok else f"http={r.status}",
                                integration_type="uon",
                            )
                    except Exception as er:
                        await write_integration_log(
                            enterprise_number=enterprise_number,
                            event_type="call_history",
                            request_data={"uniqueId": unique_id, "payload": payload},
                            response_data=None,
                            status_ok=False,
                            error_message=str(er),
                            integration_type="uon",
                        )
        except Exception as e:
            logger.error(f"❌ U-ON forward failed: {e}")

    # МойСклад → попап при dial (только live события, не recovery)
    if active.get("ms") and event_type == "dial" and origin != "download":
        try:
            import aiohttp
            # Определяем направление/номер/внутренний
            try:
                event_kind, external_phone_e164 = guess_direction_and_phone(raw, None)
            except Exception:
                event_kind, external_phone_e164 = ("in", normalize_phone_e164(str(raw.get("Phone") or "")))
            internal_code = determine_internal_code(raw)

            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Отправляем событие в МойСклад webhook
                payload = {
                    "enterprise_number": enterprise_number,
                    "phone": external_phone_e164,
                    "extension": internal_code or "",
                    "direction": event_kind,
                    "event_type": "dial",
                    "unique_id": unique_id,
                    "raw": raw
                }
                logger.info(f"🔗 Sending dial event to МойСклад: {payload}")
                try:
                    async with session.post("http://127.0.0.1:8023/internal/ms/incoming-call", json=payload) as r:
                        ok = (r.status == 200)
                        try:
                            data = await r.json()
                        except Exception:
                            data = {"status": r.status}
                        await write_integration_log(
                            enterprise_number=enterprise_number,
                            event_type="incoming_call",
                            request_data={"uniqueId": unique_id, "payload": payload},
                            response_data=data,
                            status_ok=ok,
                            error_message=None if ok else f"http={r.status}",
                            integration_type="ms",
                        )
                        if ok:
                            logger.info(f"✅ МойСклад dial notification sent successfully")
                        else:
                            logger.error(f"❌ МойСклад dial notification failed with status {r.status}")
                except Exception as er:
                    await write_integration_log(
                        enterprise_number=enterprise_number,
                        event_type="incoming_call",
                        request_data={"uniqueId": unique_id, "payload": payload},
                        response_data=None,
                        status_ok=False,
                        error_message=str(er),
                        integration_type="ms",
                    )
                    logger.error(f"❌ МойСклад dial notification exception: {er}")
        except Exception as e:
            logger.error(f"❌ МойСклад forward failed: {e}")
    
    # МойСклад → обработка hangup событий
    if active.get("ms") and event_type == "hangup":
        try:
            import aiohttp
            # Определяем направление/номер/внутренний
            try:
                event_kind, external_phone_e164 = guess_direction_and_phone(raw, None)
            except Exception:
                event_kind, external_phone_e164 = ("in", normalize_phone_e164(str(raw.get("Phone") or "")))
            internal_code = determine_internal_code(raw)

            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Выбираем endpoint в зависимости от типа события
                if origin == "download":
                    # Recovery событие - только создание звонка без попапов
                    endpoint = "http://127.0.0.1:8023/internal/ms/recovery-call"
                    logger.info(f"🔄 Sending recovery hangup event to МойСклад")
                else:
                    # Live событие - обычная обработка с попапами
                    endpoint = "http://127.0.0.1:8023/internal/ms/hangup-call"
                    logger.info(f"🔗 Sending live hangup event to МойСклад")
                
                # Отправляем событие hangup в МойСклад
                payload = {
                    "enterprise_number": enterprise_number,
                    "phone": external_phone_e164,
                    "extension": internal_code or "",
                    "direction": event_kind,
                    "event_type": "hangup",
                    "unique_id": unique_id,
                    "record_url": record_url,
                    "raw": raw,
                    "origin": origin  # Передаем флаг recovery/download
                }
                
                try:
                    async with session.post(endpoint, json=payload) as r:
                        ok = (r.status == 200)
                        try:
                            data = await r.json()
                        except Exception:
                            data = {}
                        logger.info(f"🔗 МойСклад hangup response: {r.status} - {data}")
                        
                        # Логируем результат
                        event_type_log = "recovery_call" if origin == "download" else "hangup_call"
                        await write_integration_log(
                            enterprise_number=enterprise_number,
                            event_type=event_type_log,
                            request_data={"uniqueId": unique_id, "payload": payload},
                            response_data=data,
                            status_ok=ok,
                            error_message=None if ok else f"http={r.status}",
                            integration_type="ms",
                        )
                except Exception as inner_e:
                    logger.error(f"❌ МойСклад hangup HTTP error: {inner_e}")
                    # Логируем ошибку
                    event_type_log = "recovery_call" if origin == "download" else "hangup_call"
                    await write_integration_log(
                        enterprise_number=enterprise_number,
                        event_type=event_type_log,
                        request_data={"uniqueId": unique_id, "payload": payload},
                        response_data=None,
                        status_ok=False,
                        error_message=str(inner_e),
                        integration_type="ms",
                    )
        except Exception as e:
            logger.error(f"❌ МойСклад hangup forward failed: {e}")

    return {"success": True}

# Совместимость с вызовами со слэшем на конце
@app.post("/dispatch/call-event/")
async def dispatch_call_event_slash(request: Request):
    return await dispatch_call_event(request)

@app.post("/cache/invalidate/{enterprise_number}")
async def invalidate_cache(enterprise_number: str):
    """Принудительная инвалидация кэша для предприятия"""
    await invalidate_enterprise_cache(enterprise_number)
    return {"message": f"Cache invalidated for enterprise {enterprise_number}"}

@app.put("/config/{enterprise_number}/{integration_type}")
async def update_integration_config(enterprise_number: str, integration_type: str, config_data: dict = Body(...)):
    """Обновление конфигурации интеграции в кэше"""
    global full_config_cache
    
    try:
        logger.info(f"📝 Updating {integration_type} config for enterprise {enterprise_number}")
        
        # Сначала инвалидируем кэш для предприятия
        await invalidate_enterprise_cache(enterprise_number)
        
        # Получаем текущую полную конфигурацию из БД
        if not pg_pool:
            raise HTTPException(status_code=500, detail="Database not available")
            
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1", 
                enterprise_number
            )
            
            if not row:
                raise HTTPException(status_code=404, detail="Enterprise not found")
            
            # Парсим текущую конфигурацию
            current_config = row['integrations_config'] or {}
            if isinstance(current_config, str):
                current_config = json.loads(current_config)
            
            # Обновляем конфигурацию конкретной интеграции
            current_config[integration_type] = config_data
            
            # Сохраняем обратно в БД
            await conn.execute(
                "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
                json.dumps(current_config), enterprise_number
            )
            
            logger.info(f"✅ {integration_type} config saved to DB for enterprise {enterprise_number}")
        
        # Форсируем обновление кэша для этого предприятия
        await load_enterprise_full_config(enterprise_number)
        
        logger.info(f"🔄 Cache refreshed for enterprise {enterprise_number}")
        
        return {
            "status": "success", 
            "message": f"{integration_type} configuration updated",
            "enterprise_number": enterprise_number
        }
        
    except Exception as e:
        logger.error(f"💥 Error updating {integration_type} config for {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Configuration update failed: {str(e)}")

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


@app.post("/notify/incoming")
async def notify_incoming(payload: dict = Body(...)):
    """Унифицированный вход: { enterprise_number, phone, extension } → вызвать всплывашку в primary-интеграции (сейчас U‑ON)."""
    try:
        enterprise_number = str(payload.get("enterprise_number") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        extension = str(payload.get("extension") or "").strip()

        if not enterprise_number:
            raise HTTPException(status_code=400, detail="enterprise_number required")

        if not pg_pool:
            await init_database()
        async with pg_pool.acquire() as conn:
            primary = await _get_enterprise_smart_primary(conn, enterprise_number)

        if primary == "uon":
            # Проксируем в 8022
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    forward_payload = {
                        "enterprise_number": enterprise_number,
                        "phone": phone,
                        "extension": extension,
                    }
                    try:
                        if isinstance(payload.get("extensions_all"), list):
                            forward_payload["extensions_all"] = [str(x) for x in payload["extensions_all"]]
                    except Exception:
                        pass
                    r = await client.post("http://127.0.0.1:8022/internal/uon/notify-incoming", json=forward_payload)
                    ok = (r.status_code == 200 and (r.json() or {}).get("success"))
                    return {"success": ok, "provider": "uon", "status": r.status_code}
            except Exception as e:
                logger.error(f"forward to uon failed: {e}")
                return {"success": False, "provider": "uon", "error": str(e)}

        return {"success": False, "provider": primary, "error": "provider_not_implemented"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"notify_incoming error: {e}")
        return {"success": False, "error": str(e)}


async def _get_enterprise_smart_primary(conn: asyncpg.Connection, enterprise_number: str) -> str:
    """Returns primary integration code for Smart (default: 'retailcrm')."""
    try:
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        cfg = dict(row).get("integrations_config") if row else None
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = None
        if isinstance(cfg, dict):
            smart = cfg.get("smart") or {}
            primary = smart.get("primary") or "retailcrm"
            return str(primary)
    except Exception as e:
        logger.error(f"_get_enterprise_smart_primary error: {e}")
    return "retailcrm"


@app.get("/customer-name/{enterprise_number}/{phone}")
async def get_customer_name(enterprise_number: str, phone: str):
    """Return customer display name via primary integration (always fresh)."""
    global pg_pool
    # Normalize phone to E164
    phone_e164 = normalize_phone_e164(phone)

    if not pg_pool:
        await init_database()
    if not pg_pool:
        return {"name": None}

    try:
        async with pg_pool.acquire() as conn:
            primary = await _get_enterprise_smart_primary(conn, enterprise_number)
            name: Optional[str] = None

            extra_source: dict = {}
            if primary == "retailcrm":
                # Query local retailcrm service for fresh profile data (not cached)
                url = "http://127.0.0.1:8019/internal/retailcrm/customer-profile"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            # Формируем полное имя из компонентов
                            fn = (data.get("first_name") or "").strip()
                            ln = (data.get("last_name") or "").strip()
                            mn = (data.get("middle_name") or "").strip()
                            en = (data.get("enterprise_name") or "").strip()
                            
                            # Приоритет: enterprise_name (для корпоративных), затем ФИО
                            if en:
                                name = en
                            elif fn or ln:
                                name_parts = [ln, fn, mn]
                                name = " ".join([p for p in name_parts if p])
                except Exception as e:
                    logger.warning(f"retailcrm name lookup failed: {e}")
            elif primary == "ms":
                # Query local moysklad service for customer name
                url = "http://127.0.0.1:8023/internal/ms/customer-name"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164, "enterprise_number": enterprise_number})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            n = data.get("name")
                            if isinstance(n, str) and n.strip():
                                name = n.strip()
                except Exception as e:
                    logger.warning(f"moysklad name lookup failed: {e}")
            elif primary == "uon":
                # Query local uon service for customer profile
                url = "http://127.0.0.1:8022/internal/uon/customer-by-phone"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164, "enterprise_number": enterprise_number})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            profile = data.get("profile") or {}
                            n = profile.get("display_name")
                            if isinstance(n, str) and n.strip():
                                name = n.strip()
                except Exception as e:
                    logger.warning(f"uon name lookup failed: {e}")
            else:
                # Other integrations can be added here
                name = None

            # Возвращаем свежее имя без кэширования
            return {"name": name}
    except Exception as e:
        logger.error(f"get_customer_name error: {e}")
        return {"name": None}


@app.get("/customer-profile/{enterprise_number}/{phone}")
async def get_customer_profile(enterprise_number: str, phone: str):
    """Return customer profile via primary integration (cached).
    Profile fields: last_name, first_name, middle_name, enterprise_name.
    """
    global pg_pool
    phone_e164 = normalize_phone_e164(phone)
    cache_key = f"{enterprise_number}|{phone_e164}"
    now = time.time()
    entry = customer_profile_cache.get(cache_key)
    if entry and entry.get("expires", 0) > now:
        return {
            "last_name": entry.get("last_name"),
            "first_name": entry.get("first_name"),
            "middle_name": entry.get("middle_name"),
            "enterprise_name": entry.get("enterprise_name"),
        }

    if not pg_pool:
        await init_database()
    if not pg_pool:
        return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}

    try:
        async with pg_pool.acquire() as conn:
            primary = await _get_enterprise_smart_primary(conn, enterprise_number)
            prof = {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}
            extra_source = None
            if primary == "retailcrm":
                url = "http://127.0.0.1:8019/internal/retailcrm/customer-profile"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            for k in ("last_name", "first_name", "middle_name", "enterprise_name"):
                                v = data.get(k)
                                if isinstance(v, str):
                                    prof[k] = v.strip() or None
                            
                            # Получаем сырые данные клиента из RetailCRM для merge_customer_identity
                            # Сначала ищем среди обычных клиентов
                            url_customer = "http://127.0.0.1:8019/test/search-customer"
                            try:
                                customer_resp = await client.get(url_customer, params={"phone": phone_e164})
                                if customer_resp.status_code == 200:
                                    customer_data = customer_resp.json() or {}
                                    if customer_data.get("success") and customer_data.get("data"):
                                        data_obj = customer_data.get("data", {})
                                        if data_obj.get("customers"):
                                            customers = data_obj.get("customers", [])
                                            if customers:
                                                extra_source = {"raw": customers[0], "type": "retailcrm"}
                            except Exception:
                                pass
                            
                            # Корпоративный поиск (новый эндпоинт): выполняем всегда и при наличии компании — ПРЕИМУЩЕСТВЕННО используем его
                            url_company = "http://127.0.0.1:8019/internal/retailcrm/company-by-phone/" + phone_e164
                            try:
                                company_resp = await client.get(url_company)
                                if company_resp.status_code == 200:
                                    payload = company_resp.json() or {}
                                    logger.info(f"[get_customer_profile] Company response: {payload}")
                                    if payload.get("success"):
                                        results = (payload.get("data") or {}).get("results") or []
                                        logger.info(f"[get_customer_profile] Results: {results}")
                                        if results:
                                            result = results[0]
                                            company = result.get("company", {})
                                            contacts_list = result.get("contacts") or []
                                            company_id = company.get("id")
                                            company_name = company.get("name")
                                        # Найдем контакт, чей телефон совпадает с искомым
                                        chosen_contact = None
                                        for c in contacts_list:
                                            for ph in (c.get("phones") or []):
                                                if ph == phone_e164:
                                                    chosen_contact = c
                                                    break
                                            if chosen_contact:
                                                break
                                        if not chosen_contact and contacts_list:
                                            # fallback: основной, иначе первый
                                            chosen_contact = next((c for c in contacts_list if c.get("isMain")), contacts_list[0])

                                        # Собираем corp_profile с полной матрицей контактов
                                        corp_profile = {
                                            "company_info": {"id": company_id, "name": company_name},
                                            "contacts": contacts_list,
                                        }
                                        if chosen_contact:
                                            corp_profile.update({
                                                "firstName": chosen_contact.get("firstName"),
                                                "lastName": chosen_contact.get("lastName"),
                                                "patronymic": chosen_contact.get("patronymic"),
                                                "phones": [{"number": p} for p in (chosen_contact.get("phones") or [])],
                                            })

                                        # Переопределяем extra_source на corporate
                                        extra_source = {"raw": corp_profile, "type": "retailcrm_corporate"}
                                        logger.info(f"[get_customer_profile] Corporate found - company: {company_name}, chosen_contact: {chosen_contact}")
                                        prof["first_name"] = (chosen_contact or {}).get("firstName") or prof.get("first_name")
                                        prof["last_name"] = (chosen_contact or {}).get("lastName") or prof.get("last_name")
                                        prof["middle_name"] = (chosen_contact or {}).get("patronymic") or prof.get("middle_name")
                                        prof["enterprise_name"] = company_name or prof.get("enterprise_name")
                            except Exception as e:
                                logger.warning(f"Company search failed: {e}")
                except Exception as e:
                    logger.warning(f"customer-profile retailcrm lookup failed: {e}")
            elif primary == "uon":
                url = "http://127.0.0.1:8022/internal/uon/customer-by-phone"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164, "enterprise_number": enterprise_number})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            profile = data.get("profile") or {}
                            raw_data = data.get("source", {}).get("raw") if "source" in data else None
                            
                            # Извлекаем данные профиля из U-ON формата
                            display_name = profile.get("display_name", "")
                            if display_name:
                                # Пробуем разделить display_name на части (если это "Фамилия Имя")
                                parts = display_name.split()
                                if len(parts) >= 1:
                                    prof["last_name"] = parts[0]   # ИСПРАВЛЕНО: первая часть - фамилия
                                if len(parts) >= 2:
                                    prof["first_name"] = parts[1]  # ИСПРАВЛЕНО: вторая часть - имя
                            
                            # Если есть сырые данные от U-ON, попробуем извлечь больше информации
                            if isinstance(raw_data, dict):
                                for uon_key, our_key in [
                                    ("last_name", "last_name"), ("lastName", "last_name"), ("lname", "last_name"), ("u_surname", "last_name"),
                                    ("first_name", "first_name"), ("firstName", "first_name"), ("fname", "first_name"), ("u_name", "first_name"),
                                    ("middle_name", "middle_name"), ("patronymic", "middle_name"), ("mname", "middle_name"), ("u_sname", "middle_name"),
                                    ("company", "enterprise_name"), ("enterprise_name", "enterprise_name"), ("organization", "enterprise_name")
                                ]:
                                    v = raw_data.get(uon_key)
                                    if isinstance(v, str) and v.strip():
                                        prof[our_key] = v.strip()
                                extra_source = {"raw": raw_data}
                except Exception as e:
                    logger.warning(f"customer-profile uon lookup failed: {e}")
            elif primary == "ms":
                # Получаем профиль клиента через сервис МойСклад
                logger.info(f"[get_customer_profile] МойСклад lookup for {phone_e164}")
                url = "http://127.0.0.1:8023/internal/ms/customer-debug"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164, "enterprise_number": enterprise_number})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            
                            # Получаем данные контрагента
                            counterparty = data.get("counterparty", {})
                            if counterparty:
                                counterparty_name = counterparty.get("name", "").strip()
                                
                                # Всегда устанавливаем данные (логика автогенерации проверяется в ms.py)
                                if counterparty_name:
                                    # Для МойСклад у контрагента нет отдельных полей ФИО, только name
                                    prof["last_name"] = counterparty_name
                                    prof["enterprise_name"] = counterparty_name
                                
                                # Формируем сырые данные для merge_customer_identity
                                ms_raw = {
                                    "id": counterparty.get("id"),
                                    "name": counterparty_name,
                                    "phone": phone_e164,
                                    "contact_persons": []
                                }
                                
                                # Добавляем контактные лица
                                contact_persons = data.get("contact_persons", [])
                                for contact in contact_persons:
                                    contact_name = contact.get("name", "").strip()
                                    contact_phone = contact.get("phone", "").strip()
                                    if contact_name and contact_phone:
                                        ms_raw["contact_persons"].append({
                                            "id": contact.get("id"),
                                            "name": contact_name,
                                            "phone": contact_phone
                                        })
                                
                                extra_source = {"raw": ms_raw, "type": "moysklad"}
                                logger.info(f"[get_customer_profile] МойСклад profile enriched: last_name={prof.get('last_name')}, enterprise_name={prof.get('enterprise_name')}, source_created={bool(extra_source)}")
                                
                except Exception as e:
                    logger.warning(f"customer-profile moysklad lookup failed: {e}")
            else:
                pass

            # НЕ кэшируем профиль, если есть source, чтобы всегда возвращать актуальные данные
            if not extra_source:
                customer_profile_cache[cache_key] = {**prof, "expires": now + TTL_SECONDS}
            # Возвращаем профиль, добавляя source (без кэширования source)
            logger.info(f"[get_customer_profile] Returning profile: prof={prof.keys()}, extra_source={bool(extra_source)}")
            if extra_source:
                logger.info(f"[get_customer_profile] Returning with extra_source: type={extra_source.get('type')}")
                return {**prof, "source": extra_source}
            # Если данные есть, но source не найден, добавляем дефолтный source с минимальным raw
            if any(prof.get(k) for k in ("last_name", "first_name", "enterprise_name")):
                # Создаем минимальный raw для совместимости с enrich_customer
                minimal_raw = {
                    "id": "unknown",  # Заглушка для external_id
                    "lastName": prof.get("last_name"),
                    "firstName": prof.get("first_name"),
                    "patronymic": prof.get("middle_name"),
                    "phones": [{"number": phone_e164}]
                }
                if prof.get("enterprise_name"):
                    minimal_raw["company"] = {"name": prof.get("enterprise_name")}
                    # Если есть enterprise_name, это корпоративный клиент
                    source_type = f"{primary}_corporate" if primary else "unknown_corporate"
                else:
                    source_type = primary if primary else "unknown"
                default_source = {"raw": minimal_raw, "type": source_type}
                return {**prof, "source": default_source}
            return prof
    except Exception as e:
        logger.error(f"get_customer_profile error: {e}")
        return {"last_name": None, "first_name": None, "middle_name": None, "enterprise_name": None}

@app.get("/responsible-extension/{enterprise_number}/{phone}")
async def get_responsible_extension(enterprise_number: str, phone: str, nocache: Optional[bool] = False):
    """Return responsible manager extension via primary integration (cached)."""
    global pg_pool
    phone_e164 = normalize_phone_e164(phone)
    cache_key = f"{enterprise_number}|{phone_e164}"

    now = time.time()
    if not nocache:
        entry = responsible_ext_cache.get(cache_key)
        if entry and entry.get("expires", 0) > now:
            return {"extension": entry.get("ext")}

    if not pg_pool:
        await init_database()
    if not pg_pool:
        return {"extension": None}

    try:
        async with pg_pool.acquire() as conn:
            primary = await _get_enterprise_smart_primary(conn, enterprise_number)
            ext: Optional[str] = None
            mapped_ext: Optional[str] = None
            manager_id_from_api: Optional[int] = None

            # Загружаем ручную карту user_extensions из enterprises.integrations_config
            try:
                row_cfg = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
                cfg = dict(row_cfg).get("integrations_config") if row_cfg else None
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except Exception:
                        cfg = None
                if isinstance(cfg, dict):
                    if primary == "retailcrm":
                        retail = cfg.get("retailcrm") or {}
                        user_ext_map = retail.get("user_extensions") or {}
                    elif primary == "ms":
                        ms = cfg.get("ms") or {}
                        raw_mapping = ms.get("employee_mapping") or {}
                        # Преобразуем МойСклад формат в простой для совместимости
                        user_ext_map = {}
                        for ext, emp_data in raw_mapping.items():
                            if isinstance(emp_data, dict):
                                emp_id = emp_data.get("employee_id")
                                if emp_id:
                                    user_ext_map[emp_id] = ext
                            elif isinstance(emp_data, str):
                                user_ext_map[emp_data] = ext
                    elif primary == "uon":
                        uon = cfg.get("uon") or {}
                        user_ext_map = uon.get("user_extensions") or {}
                    else:
                        user_ext_map = {}
                else:
                    user_ext_map = {}
            except Exception as e:
                logger.warning(f"read user_extensions failed: {e}")
                user_ext_map = {}

            if primary == "retailcrm":
                # Ask local retailcrm service for responsible manager (stable internal endpoint)
                url = f"http://127.0.0.1:8019/internal/retailcrm/responsible-extension"
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        for attempt in range(2):
                            try:
                                resp = await client.get(url, params={"phone": phone_e164, "enterprise_number": enterprise_number})
                                if resp.status_code == 200:
                                    data = resp.json() or {}
                                    code = data.get("extension") if isinstance(data, dict) else None
                                    mid = data.get("manager_id") if isinstance(data, dict) else None
                                    if isinstance(code, str) and code.isdigit():
                                        ext = code
                                    # МойСклад возвращает employee_id, ищем его в user_ext_map
                                    if isinstance(mid, str) and mid.strip():
                                        # user_ext_map теперь в формате employee_id -> extension
                                        mapped_extension = user_ext_map.get(mid)
                                        if mapped_extension and mapped_extension.isdigit():
                                            mapped_ext = mapped_extension
                                    logger.info(f"responsible-extension moysklad ok ext={ext} mapped={mapped_ext} employee_id={mid} phone={phone_e164}")
                                    break
                                else:
                                    logger.warning(f"responsible-extension moysklad http={resp.status_code} attempt={attempt+1}")
                            except Exception as er:
                                logger.warning(f"responsible-extension moysklad error attempt={attempt+1}: {er}")
                                await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning(f"responsible-extension retailcrm client setup failed: {e}")
            elif primary == "ms":
                # Ask local moysklad service for responsible manager
                url = f"http://127.0.0.1:8023/internal/ms/responsible-extension"
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        for attempt in range(2):
                            try:
                                resp = await client.get(url, params={"phone": phone_e164, "enterprise_number": enterprise_number})
                                if resp.status_code == 200:
                                    data = resp.json() or {}
                                    code = data.get("extension") if isinstance(data, dict) else None
                                    mid = data.get("manager_id") if isinstance(data, dict) else None
                                    if isinstance(code, str) and code.isdigit():
                                        ext = code
                                    # МойСклад возвращает employee_id, ищем его в user_ext_map
                                    if isinstance(mid, str) and mid.strip():
                                        # user_ext_map теперь в формате employee_id -> extension
                                        mapped_extension = user_ext_map.get(mid)
                                        if mapped_extension and mapped_extension.isdigit():
                                            mapped_ext = mapped_extension
                                    logger.info(f"responsible-extension moysklad ok ext={ext} mapped={mapped_ext} employee_id={mid} phone={phone_e164}")
                                    break
                                else:
                                    logger.warning(f"responsible-extension moysklad http={resp.status_code} attempt={attempt+1}")
                            except Exception as er:
                                logger.warning(f"responsible-extension moysklad error attempt={attempt+1}: {er}")
                                await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning(f"responsible-extension moysklad client setup failed: {e}")
            elif primary == "uon":
                url = f"http://127.0.0.1:8022/internal/uon/responsible-extension"
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        for attempt in range(2):
                            try:
                                resp = await client.get(url, params={"phone": phone_e164})
                                if resp.status_code == 200:
                                    data = resp.json() or {}
                                    code = data.get("extension") if isinstance(data, dict) else None
                                    mid = data.get("manager_id") if isinstance(data, dict) else None
                                    if isinstance(code, str) and code.isdigit():
                                        ext = code
                                    try:
                                        manager_id_from_api = int(mid) if str(mid).isdigit() else None
                                    except Exception:
                                        manager_id_from_api = None
                                    if manager_id_from_api is not None:
                                        mapped = user_ext_map.get(str(manager_id_from_api))
                                        if isinstance(mapped, str) and mapped.isdigit():
                                            mapped_ext = mapped
                                    logger.info(f"responsible-extension uon ok ext={ext} mapped={mapped_ext} manager_id={manager_id_from_api} phone={phone_e164}")
                                    break
                                else:
                                    logger.warning(f"responsible-extension uon http={resp.status_code} attempt={attempt+1}")
                            except Exception as er:
                                logger.warning(f"responsible-extension uon error attempt={attempt+1}: {er}")
                                await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning(f"responsible-extension uon client setup failed: {e}")
            else:
                ext = None

            # Выбор окончательного значения: приоритет mapped_ext, затем ext из API
            final_ext = mapped_ext or ext
            responsible_ext_cache[cache_key] = {"ext": final_ext, "expires": now + TTL_SECONDS}
            return {"extension": final_ext}
    except Exception as e:
        logger.error(f"get_responsible_extension error: {e}")
        return {"extension": None}

@app.post("/enrich-customer/{enterprise_number}/{phone_e164}")
async def enrich_customer(enterprise_number: str, phone_e164: str):
    """
    🔄 УНИВЕРСАЛЬНЫЙ ENDPOINT ДЛЯ ОБОГАЩЕНИЯ ПРОФИЛЯ КЛИЕНТА
    
    Получает профиль клиента из всех доступных CRM, определяет приоритетную интеграцию,
    обновляет данные в customers со всеми связанными номерами через person_uid.
    
    Returns:
    {
        "success": true,
        "full_name": "Фамилия Имя Отчество", 
        "source": "retailcrm",
        "external_id": "12345",
        "person_uid": "retailcrm:12345",
        "linked_phones": ["+375296254070", "+375297003134"],
        "updated_count": 2
    }
    """
    try:
        logger.info(f"[enrich-customer] Starting enrichment for {enterprise_number}/{phone_e164}")
        
        # 1. Получаем конфигурацию интеграций предприятия
        global pg_pool
        if not pg_pool:
            return {"success": False, "error": "Database connection failed"}
            
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT integrations_config FROM enterprises WHERE number = $1",
                enterprise_number
            )
            if not row or not row["integrations_config"]:
                return {"success": False, "error": "No integrations configured"}
                
            integrations_config = row["integrations_config"]
            if isinstance(integrations_config, str):
                import json
                try:
                    integrations_config = json.loads(integrations_config)
                except Exception:
                    integrations_config = {}
            
            primary_integration = integrations_config.get("smart", {}).get("primary") if isinstance(integrations_config, dict) else None
            # Fallback по умолчанию: используем retailcrm как primary, если явно не задано
            if not primary_integration:
                primary_integration = "retailcrm"
        
        # 2. Получаем профиль клиента из CRM
        prof = await get_customer_profile(enterprise_number, phone_e164)
        logger.info(f"[enrich-customer] Profile from CRM: {prof}")
        if not prof:
            return {"success": False, "error": "No customer profile found"}
            
        source_info = prof.get("source", {})
        source_raw = source_info.get("raw")
        source_type = source_info.get("type", "").lower()
        
        if not source_raw or not source_type:
            return {"success": False, "error": "Invalid profile data"}
        
        # 3. Извлекаем ФИО из профиля
        fn = (prof.get("first_name") or "").strip()
        ln = (prof.get("last_name") or "").strip() 
        mn = (prof.get("middle_name") or "").strip()
        en = (prof.get("enterprise_name") or "").strip()
        
        if not (fn or ln or en):
            return {"success": False, "error": "No name data in profile"}
        
        # 4. Определяем external_id в зависимости от CRM
        external_id = None
        if source_type == "uon":
            for key in ("client_id", "id", "customer_id", "clientId"):
                ext_id = source_raw.get(key)
                if isinstance(ext_id, (str, int)) and str(ext_id).strip():
                    external_id = str(ext_id).strip()
                    break
        elif source_type == "retailcrm":
            ext_id = source_raw.get("id")
            if isinstance(ext_id, (str, int)) and str(ext_id).strip():
                external_id = str(ext_id).strip()
        elif source_type == "retailcrm_corporate":
            # Для корпоративных клиентов используем ID компании
            company_info = source_raw.get("company_info", {})
            company_id = company_info.get("id")
            if isinstance(company_id, (str, int)) and str(company_id).strip():
                external_id = str(company_id).strip()
        elif source_type == "moysklad":
            # Для МойСклад используем ID контрагента
            ext_id = source_raw.get("id")
            if isinstance(ext_id, (str, int)) and str(ext_id).strip():
                external_id = str(ext_id).strip()
        
        if not external_id:
            return {"success": False, "error": f"No external_id found for {source_type}"}
        
        # 🔥 НОВАЯ ЛОГИКА: Извлекаем ВСЕ номера клиента из CRM
        all_client_phones = []
        if source_type == "retailcrm":
            # У RetailCRM в source_raw.phones есть массив номеров
            phones_data = source_raw.get("phones", [])
            if isinstance(phones_data, list):
                for phone_entry in phones_data:
                    if isinstance(phone_entry, dict):
                        phone_num = phone_entry.get("number", "").strip()
                        if phone_num and phone_num.startswith("+"):
                            all_client_phones.append(phone_num)
        elif source_type == "retailcrm_corporate":
            # Для корпоративных клиентов собираем телефоны ВСЕХ контактных лиц компании
            # source_raw содержит: company_info, contacts, phones (выбранного контакта)
            contacts_data = source_raw.get("contacts") or []
            if isinstance(contacts_data, list):
                for contact in contacts_data:
                    contact_phones = contact.get("phones") or []
                    # phones - это список строк для корпоративных контактов
                    for phone_str in contact_phones:
                        if isinstance(phone_str, str) and phone_str.strip().startswith("+"):
                            all_client_phones.append(phone_str.strip())
        elif source_type == "moysklad":
            # Для МойСклад собираем все номера контрагента и контактных лиц
            # Основной номер контрагента
            if hasattr(source_raw, 'get'):
                counterparty_phone = source_raw.get("phone", "").strip()
                if counterparty_phone and counterparty_phone.startswith("+"):
                    all_client_phones.append(counterparty_phone)
            
            # Номера контактных лиц
            contact_persons = source_raw.get("contact_persons", []) if hasattr(source_raw, 'get') else []
            if isinstance(contact_persons, list):
                for contact in contact_persons:
                    if isinstance(contact, dict):
                        contact_phone = contact.get("phone", "").strip()
                        if contact_phone and contact_phone.startswith("+"):
                            all_client_phones.append(contact_phone)
            
            # Fallback: добавляем текущий номер если ничего не найдено
            if not all_client_phones:
                all_client_phones.append(phone_e164)
        elif source_type == "uon":
            # Для U-ON можно добавить аналогичную логику, если данные доступны
            # Пока добавляем только текущий номер
            all_client_phones.append(phone_e164)
        else:
            all_client_phones.append(phone_e164)
        
        # Удаляем дублирование и нормализуем
        all_client_phones = list(set(all_client_phones))
        logger.info(f"[enrich-customer] Found {len(all_client_phones)} phone(s) for client: {all_client_phones}")
        
        # 5. 🔥 НОВАЯ ЛОГИКА: Обрабатываем ВСЕ номера клиента и объединяем их под одним person_uid
        updated_count = 0
        person_uid = None
        linked_phones = []
        
        # Для корпоративных клиентов подготовим карту телефон → contact_id и подтянем название компании
        phone_to_contact_id: Dict[str, Any] = {}
        if source_type == "retailcrm_corporate":
            try:
                company_info = (source_raw or {}).get("company_info") or {}
                en = company_info.get("name") or en  # обновим enterprise_name, если есть
                for contact in (source_raw or {}).get("contacts") or []:
                    contact_id = contact.get("id")
                    contact_phones = contact.get("phones") or []
                    # phones - это список строк для корпоративных контактов
                    for phone_str in contact_phones:
                        if isinstance(phone_str, str) and phone_str.strip():
                            phone_to_contact_id[phone_str.strip()] = contact_id
            except Exception:
                phone_to_contact_id = {}
        
        # Источник считается первичным, если совпадает с primary,
        # либо если primary == retailcrm и источник retailcrm_corporate
        is_primary_src = (
            source_type == primary_integration or
            (primary_integration == "retailcrm" and source_type in ("retailcrm", "retailcrm_corporate"))
        )

        if is_primary_src:
            from app.services.customers import merge_customer_identity, update_fio_for_person
            
            # 📞 Обрабатываем ВСЕ номера клиента из CRM
            existing_person_uids = set()
            primary_person_uid = None
            
            async with pg_pool.acquire() as conn:
                # 🔍 Проверяем существующие записи для всех номеров клиента
                for phone in all_client_phones:
                    existing_row = await conn.fetchrow(
                        "SELECT meta FROM customers WHERE enterprise_number = $1 AND phone_e164 = $2",
                        enterprise_number, phone
                    )
                    if existing_row and existing_row["meta"]:
                        try:
                            meta = existing_row["meta"]
                            if isinstance(meta, str):
                                import json
                                meta = json.loads(meta)
                            existing_uid = meta.get("person_uid")
                            if existing_uid:
                                existing_person_uids.add(existing_uid)
                        except Exception:
                            pass
                
                # 🎯 Определяем основной person_uid (приоритет - текущий source_type)
                if source_type == "retailcrm_corporate":
                    # Для корпоративных используем специальный формат с company_id
                    target_person_uid = f"retailcrm_corp:{external_id}"
                else:
                    target_person_uid = f"{source_type}:{external_id}"
                if target_person_uid in existing_person_uids:
                    primary_person_uid = target_person_uid
                elif existing_person_uids:
                    # Если есть другие person_uid, выбираем первый (будем мигрировать к основному)
                    primary_person_uid = list(existing_person_uids)[0]
                else:
                    # Создаем новый
                    primary_person_uid = target_person_uid
                
                logger.info(f"[enrich-customer] Using person_uid: {primary_person_uid}, merging from: {existing_person_uids}")
            
            # 📝 Обрабатываем каждый номер клиента
            for phone in all_client_phones:
                # Для corporate формируем per-phone source_raw с конкретным contact_id и ФИО
                per_phone_source_raw = source_raw
                phone_fio = {
                    "first_name": fn if fn else None,
                    "last_name": ln if ln else None,
                    "middle_name": mn if mn else None,
                    "enterprise_name": en if en else None
                }
                
                if source_type == "retailcrm_corporate":
                    try:
                        import copy
                        per_phone_source_raw = copy.deepcopy(source_raw) if source_raw else {}
                        per_phone_source_raw.setdefault("company_info", {})
                        per_phone_source_raw["company_info"]["contact_id"] = phone_to_contact_id.get(phone)
                        
                        # Найдем конкретные ФИО для этого номера из contacts
                        contacts_data = source_raw.get("contacts") or []
                        for contact in contacts_data:
                            contact_phones = contact.get("phones") or []
                            if phone in contact_phones:
                                # Используем ФИО конкретного контакта
                                phone_fio = {
                                    "first_name": (contact.get("firstName") or "").strip() or None,
                                    "last_name": (contact.get("lastName") or "").strip() or None,
                                    "middle_name": (contact.get("patronymic") or "").strip() or None,
                                    "enterprise_name": en if en else None
                                }
                                break
                    except Exception:
                        per_phone_source_raw = source_raw
                        
                await merge_customer_identity(
                    enterprise_number=enterprise_number,
                    phone_e164=phone,
                    source=source_type,
                    external_id=external_id,
                    fio=phone_fio,
                    set_primary=True,
                    person_uid=primary_person_uid,
                    source_raw=per_phone_source_raw
                )
            
            # 🔗 Объединяем все номера под одним person_uid
            async with pg_pool.acquire() as conn:
                # Обновляем person_uid для всех номеров клиента
                for phone in all_client_phones:
                    await conn.execute("""
                        UPDATE customers 
                        SET meta = jsonb_set(
                            COALESCE(meta, '{}'::jsonb), 
                            '{person_uid}', 
                            $3::jsonb
                        )
                        WHERE enterprise_number = $1 AND phone_e164 = $2
                    """, enterprise_number, phone, f'"{primary_person_uid}"')
                
                # ФИО уже обновлены индивидуально для каждого номера выше
                # Для корпоративных клиентов не делаем глобальное обновление ФИО
                
                # 🧹 ОЧИСТКА УСТАРЕВШИХ СВЯЗЕЙ
                # Находим номера в БД с этим person_uid, которых НЕТ в актуальном списке CRM
                all_linked_rows = await conn.fetch(
                    "SELECT phone_e164 FROM customers WHERE enterprise_number = $1 AND meta->>'person_uid' = $2",
                    enterprise_number, primary_person_uid
                )
                all_linked_phones = [row["phone_e164"] for row in all_linked_rows]
                
                # Номера, которые нужно удалить (есть в БД, но НЕТ в CRM)
                phones_to_remove = [phone for phone in all_linked_phones if phone not in all_client_phones]
                
                if phones_to_remove:
                    logger.info(f"[enrich-customer] 🗑️ Removing outdated phone links: {phones_to_remove}")
                    for phone_to_remove in phones_to_remove:
                        # Удаляем person_uid и external_id для устаревших номеров
                        await conn.execute("""
                            UPDATE customers 
                            SET meta = jsonb_set(
                                jsonb_set(
                                    COALESCE(meta, '{}'::jsonb), 
                                    '{person_uid}', 
                                    'null'::jsonb
                                ),
                                '{ids}',
                                '{}'::jsonb
                            )
                            WHERE enterprise_number = $1 AND phone_e164 = $2
                        """, enterprise_number, phone_to_remove)
                        
                        logger.info(f"[enrich-customer] ✅ Cleaned outdated link for {phone_to_remove}")
                
                # Получаем финальный список связанных номеров (только актуальных)
                linked_rows = await conn.fetch(
                    "SELECT phone_e164 FROM customers WHERE enterprise_number = $1 AND meta->>'person_uid' = $2",
                    enterprise_number, primary_person_uid
                )
                linked_phones = [row["phone_e164"] for row in linked_rows]
                updated_count = len(linked_phones)
                person_uid = primary_person_uid
                
        else:
            # Для НЕ приоритетных интеграций только записываем external_id без обновления ФИО
            from app.services.customers import merge_customer_identity
            await merge_customer_identity(
                enterprise_number=enterprise_number,
                phone_e164=phone_e164,
                source=source_type,
                external_id=external_id,
                fio=None,  # НЕ обновляем ФИО для не приоритетных
                set_primary=False
            )
            linked_phones = [phone_e164]
            updated_count = 1
        
        # 6. Формируем полное имя для ответа
        full_name_parts = []
        if ln: full_name_parts.append(ln)
        if fn: full_name_parts.append(fn)
        if mn: full_name_parts.append(mn)
        full_name = " ".join(full_name_parts)
        
        logger.info(f"[enrich-customer] SUCCESS: {phone_e164} -> {full_name} (source: {source_type}, primary: {is_primary_src})")
        
        return {
            "success": True,
            "full_name": full_name,
            "source": source_type,
            "external_id": external_id,
            "person_uid": person_uid,
            "linked_phones": linked_phones,
            "updated_count": updated_count,
            "is_primary_source": is_primary_src
        }
        
    except Exception as e:
        logger.error(f"[enrich-customer] ERROR for {enterprise_number}/{phone_e164}: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

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
