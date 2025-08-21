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
cache_stats = {
    "hits": 0,
    "misses": 0,
    "refreshes": 0,
    "cache_size": 0,
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

# ===== Вспомогательные функции =====

async def fetch_enterprise_by_token(token: str) -> Optional[str]:
    """Возвращает enterprise_number по токену (name2 или secret)."""
    if not pg_pool:
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
            return row["number"] if row else None
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
    
    return {
        **cache_stats,
        "hit_rate_percent": round(hit_rate, 2),
        "cache_entries": len(integration_cache),
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
    
    raise HTTPException(status_code=404, detail="Enterprise not found")

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

    if not token or not unique_id or event_type not in {"dial", "hangup"}:
        raise HTTPException(status_code=400, detail="invalid payload")

    enterprise_number = await fetch_enterprise_by_token(token)
    if not enterprise_number:
        raise HTTPException(status_code=404, detail="enterprise not found by token")

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
                    duration_val = raw.get("Duration") or raw.get("Billsec") or 0
                    try:
                        duration = int(duration_val)
                    except Exception:
                        duration = 0
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
                    }
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
    """Return customer display name via primary integration (cached)."""
    global pg_pool
    # Normalize phone to E164
    phone_e164 = normalize_phone_e164(phone)
    cache_key = f"{enterprise_number}|{phone_e164}"

    # Check cache
    now = time.time()
    entry = customer_name_cache.get(cache_key)
    if entry and entry.get("expires", 0) > now:
        return {"name": entry.get("name")}

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
                # Query local retailcrm service for name
                url = "http://127.0.0.1:8019/internal/retailcrm/customer-name"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164})
                        if resp.status_code == 200:
                            data = resp.json() or {}
                            n = data.get("name")
                            if isinstance(n, str) and n.strip():
                                name = n.strip()
                except Exception as e:
                    logger.warning(f"retailcrm name lookup failed: {e}")
            elif primary == "uon":
                # Query local uon service for customer profile
                url = "http://127.0.0.1:8022/internal/uon/customer-by-phone"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164})
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

            # Store in cache (TTL 90s)
            customer_name_cache[cache_key] = {"name": name, "expires": now + TTL_SECONDS}
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
                except Exception as e:
                    logger.warning(f"customer-profile retailcrm lookup failed: {e}")
            elif primary == "uon":
                url = "http://127.0.0.1:8022/internal/uon/customer-by-phone"
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        resp = await client.get(url, params={"phone": phone_e164})
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
                                    prof["first_name"] = parts[0]
                                if len(parts) >= 2:
                                    prof["last_name"] = parts[1]
                            
                            # Если есть сырые данные от U-ON, попробуем извлечь больше информации
                            if isinstance(raw_data, dict):
                                for uon_key, our_key in [
                                    ("last_name", "last_name"), ("lastName", "last_name"), ("lname", "last_name"),
                                    ("first_name", "first_name"), ("firstName", "first_name"), ("fname", "first_name"),
                                    ("middle_name", "middle_name"), ("patronymic", "middle_name"), ("mname", "middle_name"),
                                    ("company", "enterprise_name"), ("enterprise_name", "enterprise_name"), ("organization", "enterprise_name")
                                ]:
                                    v = raw_data.get(uon_key)
                                    if isinstance(v, str) and v.strip():
                                        prof[our_key] = v.strip()
                                extra_source = {"raw": raw_data}
                except Exception as e:
                    logger.warning(f"customer-profile uon lookup failed: {e}")
            else:
                pass

            customer_profile_cache[cache_key] = {**prof, "expires": now + TTL_SECONDS}
            # Возвращаем профиль, добавляя source (без кэширования source)
            if extra_source:
                return {**prof, "source": extra_source}
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
                                resp = await client.get(url, params={"phone": phone_e164})
                                if resp.status_code == 200:
                                    data = resp.json() or {}
                                    code = data.get("extension") if isinstance(data, dict) else None
                                    mid = data.get("manager_id") if isinstance(data, dict) else None
                                    if isinstance(code, str) and code.isdigit():
                                        ext = code
                                    # попробуем проставить manager_id даже если code не пришёл
                                    try:
                                        manager_id_from_api = int(mid) if str(mid).isdigit() else None
                                    except Exception:
                                        manager_id_from_api = None
                                    if manager_id_from_api is not None:
                                        # приоритет: ручная карта user_extensions
                                        mapped = user_ext_map.get(str(manager_id_from_api))
                                        if isinstance(mapped, str) and mapped.isdigit():
                                            mapped_ext = mapped
                                    logger.info(f"responsible-extension retailcrm ok ext={ext} mapped={mapped_ext} manager_id={manager_id_from_api} phone={phone_e164}")
                                    break
                                else:
                                    logger.warning(f"responsible-extension retailcrm http={resp.status_code} attempt={attempt+1}")
                            except Exception as er:
                                logger.warning(f"responsible-extension retailcrm error attempt={attempt+1}: {er}")
                                await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning(f"responsible-extension retailcrm client setup failed: {e}")
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
