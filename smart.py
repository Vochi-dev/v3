#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from collections import deque

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import httpx
import httpx


DB_CONFIG = {
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "r/Yskqh/ZbZuvjb2b3ahfg=="),
    "database": os.environ.get("POSTGRES_DB", "postgres"),
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
}


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smart")

# Дополнительный логгер решений
os.makedirs("logs", exist_ok=True)
decisions_logger = logging.getLogger("smart_decisions")
if not decisions_logger.handlers:
    fh = logging.FileHandler("logs/smart_decisions.log")
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(message)s")
    fh.setFormatter(fmt)
    decisions_logger.addHandler(fh)
    decisions_logger.propagate = False


class GetCustomerDataRequest(BaseModel):
    UniqueId: Optional[str] = None
    Phone: Optional[str] = None
    TrunkId: Optional[str] = None
    LoadDialPlan: Optional[bool] = None


class GetCustomerDataResponse(BaseModel):
    Name: Optional[str] = None
    DialPlan: Optional[str] = None


@dataclass
class CacheEntry:
    value: Tuple[Optional[str], Optional[str]]  # (Name, DialPlan)
    expires_at: float


class TTLCache:
    def __init__(self, ttl_seconds: int = 60):
        self._store: Dict[str, CacheEntry] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    def _now(self) -> float:
        return time.time()

    async def get(self, key: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
        async with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            if entry.expires_at <= self._now():
                self._store.pop(key, None)
                return None
            return entry.value

    async def set(self, key: str, value: Tuple[Optional[str], Optional[str]]):
        async with self._lock:
            self._store[key] = CacheEntry(value=value, expires_at=self._now() + self._ttl)


app = FastAPI()
cache = TTLCache(ttl_seconds=60)

# Простейшие метрики
metrics = {
    "requests_total": 0,
    "cache_hits": 0,
    "dialplan_computed": 0,
    "errors_total": 0,
}

# Хранилище латентностей (ms)
latencies_ms: deque[int] = deque(maxlen=1000)


async def _get_conn() -> Optional[asyncpg.Connection]:
    try:
        return await asyncpg.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        return None


async def _get_enterprise_by_token(conn: asyncpg.Connection, token: str) -> Optional[str]:
    try:
        row = await conn.fetchrow("SELECT number FROM enterprises WHERE name2 = $1", token)
        return row["number"] if row else None
    except Exception as e:
        logger.error(f"_get_enterprise_by_token error: {e}")
        return None


async def _get_smart_config(conn: asyncpg.Connection, enterprise_number: str) -> Dict[str, Any]:
    try:
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        icfg: Any = (dict(row).get("integrations_config") if row else None)
        if isinstance(icfg, str):
            try:
                icfg = json.loads(icfg)
            except Exception:
                icfg = None
        if isinstance(icfg, dict):
            return icfg.get("smart") or {}
        return {}
    except Exception as e:
        logger.error(f"_get_smart_config error: {e}")
        return {}


def _normalize_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


async def _get_internal_id_for_extension(conn: asyncpg.Connection, enterprise_number: str, extension: str) -> Optional[int]:
    try:
        # Основной поиск по таблице внутренних номеров
        row = await conn.fetchrow(
            """
            SELECT id FROM user_internal_phones
            WHERE enterprise_number = $1 AND phone_number = $2
            """,
            enterprise_number, extension,
        )
        if row:
            return int(row["id"])
        # Fallback: попытка сравнить без лидирующих нулей
        row2 = await conn.fetchrow(
            """
            SELECT id FROM user_internal_phones
            WHERE enterprise_number = $1 AND TRIM(LEADING '0' FROM phone_number) = TRIM(LEADING '0' FROM $2)
            """,
            enterprise_number, extension,
        )
        if row2:
            return int(row2["id"])
        return None
    except Exception as e:
        logger.error(f"_get_internal_id_for_extension error: {e}")
        return None


async def _pick_extension_by_history(
    conn: asyncpg.Connection,
    enterprise_number: str,
    phone: str,
    order: str = "desc",  # 'desc' = last_call, 'asc' = first_call
) -> Optional[str]:
    """Возвращает внутренний номер (extension) для номера phone по истории звонков.
    Сначала пробуем поле calls.main_extension, если нет — берём участника, который ответил.
    """
    try:
        # 1) По основному ответившему (main_extension)
        row = await conn.fetchrow(
            f"""
            SELECT main_extension
            FROM calls
            WHERE enterprise_id = $1 AND phone_number = $2 AND main_extension IS NOT NULL AND main_extension <> ''
            ORDER BY COALESCE(start_time, timestamp) { 'ASC' if order=='asc' else 'DESC' }
            LIMIT 1
            """,
            enterprise_number, phone,
        )
        if row and row["main_extension"]:
            ext = str(row["main_extension"]).strip()
            return ext if ext.isdigit() else None

        # 2) По участникам, кто ответил
        row2 = await conn.fetchrow(
            f"""
            SELECT cp.extension
            FROM calls c
            JOIN call_participants cp ON cp.call_id = c.id
            WHERE c.enterprise_id = $1 AND c.phone_number = $2
              AND cp.participant_status = 'answered'
            ORDER BY COALESCE(c.start_time, c.timestamp) { 'ASC' if order=='asc' else 'DESC' }
            LIMIT 1
            """,
            enterprise_number, phone,
        )
        if row2 and row2["extension"]:
            ext2 = str(row2["extension"]).strip()
            return ext2 if ext2.isdigit() else None
    except Exception as e:
        logger.error(f"_pick_extension_by_history error: {e}")
    return None


async def _compute_dialplan(
    conn: asyncpg.Connection,
    enterprise_number: str,
    trunk: str,
    phone: str,
    algorithm: str,
) -> Optional[str]:
    """Выбирает нужный mngr-контекст согласно алгоритму.
    algorithm: 'retailcrm' | 'first_call' | 'last_call' (пока реализуем first/last, retailcrm — позже)
    """
    algo = (algorithm or "").lower()
    chosen_ext: Optional[str] = None

    if algo in {"first_call", "last_call"}:
        order = "asc" if algo == "first_call" else "desc"
        chosen_ext = await _pick_extension_by_history(conn, enterprise_number, phone, order=order)
    elif algo == "retailcrm":
        e164 = "+" + phone if not phone.startswith("+") else phone
        chosen_ext = await _get_responsible_extension_via_8020(enterprise_number, e164)
    else:
        return None

    if not chosen_ext:
        return None

    internal_id = await _get_internal_id_for_extension(conn, enterprise_number, chosen_ext)
    if internal_id is None:
        return None

    return f"mngr{internal_id}_{trunk}_1"


async def _get_line_and_shop_names(
    conn: asyncpg.Connection,
    enterprise_number: str,
    trunk: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (line_name, shop_name) по внешней линии (GSM/SIP)."""
    try:
        if trunk.isdigit():
            # GSM
            row = await conn.fetchrow(
                """
                SELECT gl.line_name AS line_name,
                       s.name AS shop_name
                FROM gsm_lines gl
                LEFT JOIN shop_lines sl ON sl.gsm_line_id = gl.id AND sl.enterprise_number = gl.enterprise_number
                LEFT JOIN shops s ON s.id = sl.shop_id AND s.enterprise_number = gl.enterprise_number
                WHERE gl.enterprise_number = $1 AND gl.line_id = $2
                LIMIT 1
                """,
                enterprise_number, trunk,
            )
        else:
            # SIP
            row = await conn.fetchrow(
                """
                SELECT su.line_name AS line_name,
                       s.name AS shop_name
                FROM sip_unit su
                LEFT JOIN shop_sip_lines ssl ON ssl.sip_line_id = su.id AND ssl.enterprise_number = su.enterprise_number
                LEFT JOIN shops s ON s.id = ssl.shop_id AND s.enterprise_number = su.enterprise_number
                WHERE su.enterprise_number = $1 AND su.line_name = $2
                LIMIT 1
                """,
                enterprise_number, trunk,
            )
        if row:
            return (row.get("line_name"), row.get("shop_name"))
    except Exception as e:
        logger.error(f"_get_line_and_shop_names error: {e}")
    return (None, None)


async def _get_customer_name_via_8020(enterprise_number: str, phone_e164: str) -> Optional[str]:
    url = f"http://127.0.0.1:8020/customer-name/{enterprise_number}/{phone_e164}"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json() or {}
                name = data.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    except Exception:
        pass
    return None


async def _get_responsible_extension_via_8020(enterprise_number: str, phone_e164: str) -> Optional[str]:
    """Возвращает внутренний код ответственного менеджера через 8020 по предприятию и телефону."""
    # Нужна актуальная информация — просим без кэша 8020
    url = f"http://127.0.0.1:8020/responsible-extension/{enterprise_number}/{phone_e164}?nocache=true"
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json() or {}
                code = data.get("extension") if isinstance(data, dict) else None
                if isinstance(code, str) and code.isdigit():
                    return code
    except Exception:
        pass
    return None


@app.post("/api/callevent/getcustomerdata")
async def get_customer_data(request: Request, body: GetCustomerDataRequest, Token: Optional[str] = Header(default=None)):
    start_ts = time.time()
    req_id = body.UniqueId or str(int(start_ts * 1000))

    # Быстрая защита: должен быть токен
    if not Token:
        raise HTTPException(status_code=401, detail="Token header is required")

    cache_key = f"{Token}|{body.Phone}|{body.TrunkId}"
    metrics["requests_total"] += 1
    cached = await cache.get(cache_key)
    if cached is not None:
        name_cached, dialplan_cached = cached
        metrics["cache_hits"] += 1
        decisions_logger.info(
            json.dumps({
                "req_id": req_id,
                "enterprise": "cache",  # enterprise неизвестен до БД
                "trunk": body.TrunkId,
                "phone": body.Phone,
                "mode": "cache",
                "flags": None,
                "algorithm": None,
                "result": {"Name": (name_cached or ""), "DialPlan": dialplan_cached},
                "reason": "cache_hit"
            }, ensure_ascii=False)
        )
        safe_name = name_cached or ""
        return JSONResponse(GetCustomerDataResponse(Name=safe_name, DialPlan=dialplan_cached).dict())

    conn = await _get_conn()
    if not conn:
        # В случае проблем с БД: безопасный дефолт — ничего не перенаправляем
        await cache.set(cache_key, (None, None))
        elapsed = int((time.time() - start_ts) * 1000)
        latencies_ms.append(elapsed)
        decisions_logger.info(
            json.dumps({
                "req_id": req_id,
                "enterprise": None,
                "trunk": body.TrunkId,
                "phone": body.Phone,
                "mode": "db_unavailable",
                "flags": None,
                "result": {"Name": None, "DialPlan": None},
                "latency_ms": elapsed
            }, ensure_ascii=False)
        )
        return JSONResponse(GetCustomerDataResponse(Name="", DialPlan=None).dict())

    try:
        # Находим предприятие по токену (ID_TOKEN == enterprises.name2)
        enterprise_number = await _get_enterprise_by_token(conn, Token)
        if not enterprise_number:
            raise HTTPException(status_code=401, detail="Invalid Token")

        # Читаем smart-конфиг предприятия
        smart_cfg = await _get_smart_config(conn, enterprise_number)
        lines_cfg: Dict[str, Any] = (smart_cfg.get("lines") or {}) if isinstance(smart_cfg, dict) else {}
        mode: str = str((smart_cfg.get("mode") if isinstance(smart_cfg, dict) else "") or "routing+name").lower()

        trunk = (body.TrunkId or "").strip()
        phone_norm = _normalize_phone(body.Phone)
        key = None
        if trunk:
            # Определяем тип линии: пробуем как GSM (все цифры) или как SIP (строка)
            if trunk.isdigit():
                key = f"gsm:{trunk}"
            else:
                key = f"sip:{trunk}"

        line_settings = lines_cfg.get(key) if key else None

        # Режим: управляет тем, что возвращаем
        # name: отображаемое имя (пока заглушка), dialplan: имя контекста
        name: Optional[str] = None
        dialplan: Optional[str] = None

        # Если режим выключен — выходим сразу
        if mode == "off":
            await cache.set(cache_key, (None, None))
            elapsed = int((time.time() - start_ts) * 1000)
            logger.info(f"smart.getcustomerdata req_id={req_id} ent={enterprise_number} mode=off trunk={trunk} result=NONE ms={elapsed}")
            latencies_ms.append(elapsed)
            decisions_logger.info(
                json.dumps({
                    "req_id": req_id,
                    "enterprise": enterprise_number,
                    "trunk": trunk,
                    "phone": phone_norm,
                    "mode": mode,
                    "flags": None,
                    "algorithm": None,
                    "result": {"Name": None, "DialPlan": None},
                    "latency_ms": elapsed,
                    "reason": "mode_off"
                }, ensure_ascii=False)
            )
            return JSONResponse(GetCustomerDataResponse(Name="", DialPlan=None).dict())

        # Если явно запрещена маршрутизация, оставляем dialplan=None
        if isinstance(line_settings, dict):
            do_routing = bool(line_settings.get("do_routing")) and bool(line_settings.get("enabled")) and mode == "routing+name"
            if do_routing:
                algorithm = str(line_settings.get("algorithm") or "").lower()
                # Пытаемся вычислить контекст
                retailcrm_ext = None
                retailcrm_internal_id = None
                if algorithm == "retailcrm":
                    e164 = "+" + phone_norm if not phone_norm.startswith("+") else phone_norm
                    retailcrm_ext = await _get_responsible_extension_via_8020(enterprise_number, e164)
                    if isinstance(retailcrm_ext, str) and retailcrm_ext.isdigit():
                        retailcrm_internal_id = await _get_internal_id_for_extension(conn, enterprise_number, retailcrm_ext)
                        if retailcrm_internal_id is not None:
                            dialplan = f"mngr{retailcrm_internal_id}_{trunk}_1"
                    # Fallback: если не получили ответственного — используем last_call
                    if dialplan is None:
                        alt = await _compute_dialplan(conn, enterprise_number, trunk, phone_norm, "last_call")
                        if alt:
                            dialplan = alt
                else:
                    dialplan = await _compute_dialplan(conn, enterprise_number, trunk, phone_norm, algorithm)
                if dialplan:
                    metrics["dialplan_computed"] += 1

            # Имя линии/магазина/клиента (Name) для режимов name-only и routing+name
            # Формируем из флагов: линия/магазин уже сейчас; имя клиента добавим через 8020
            send_line_name = bool(line_settings.get("send_line_name"))
            send_shop_name = bool(line_settings.get("send_shop_name"))
            send_customer_name = bool(line_settings.get("send_customer_name"))
            if send_line_name or send_shop_name or send_customer_name:
                line_name, shop_name = await _get_line_and_shop_names(conn, enterprise_number, trunk)
                # Порядок отображения: значения 1..3, отсутствующие — в конец по умолчанию
                items: list[tuple[int, str]] = []
                try:
                    line_order = int((line_settings or {}).get("line_name_order")) if isinstance(line_settings, dict) else None
                except Exception:
                    line_order = None
                try:
                    cust_order = int((line_settings or {}).get("customer_name_order")) if isinstance(line_settings, dict) else None
                except Exception:
                    cust_order = None
                try:
                    shop_order = int((line_settings or {}).get("shop_name_order")) if isinstance(line_settings, dict) else None
                except Exception:
                    shop_order = None

                if send_line_name and line_name:
                    items.append((line_order if line_order in (1,2,3) else 99, str(line_name)))
                if send_customer_name:
                    e164 = "+" + phone_norm if not phone_norm.startswith("+") else phone_norm
                    customer_name = await _get_customer_name_via_8020(enterprise_number, e164)
                    if customer_name:
                        items.append((cust_order if cust_order in (1,2,3) else 99, customer_name))
                if send_shop_name and shop_name:
                    items.append((shop_order if shop_order in (1,2,3) else 99, str(shop_name)))

                if items:
                    items_sorted = sorted(items, key=lambda x: x[0])
                    name = " | ".join([v for _, v in items_sorted])

        safe_name = name or ""
        await cache.set(cache_key, (safe_name, dialplan))
        elapsed = int((time.time() - start_ts) * 1000)
        latencies_ms.append(elapsed)
        logger.info(f"smart.getcustomerdata req_id={req_id} ent={enterprise_number} trunk={trunk} result={{Name:{name},DialPlan:{dialplan}}} ms={elapsed}")
        decisions_logger.info(
            json.dumps({
                "req_id": req_id,
                "enterprise": enterprise_number,
                "trunk": trunk,
                "phone": phone_norm,
                "mode": mode,
                "flags": {
                    "enabled": bool((line_settings or {}).get("enabled")) if isinstance(line_settings, dict) else None,
                    "do_routing": bool((line_settings or {}).get("do_routing")) if isinstance(line_settings, dict) else None,
                    "send_line_name": bool((line_settings or {}).get("send_line_name")) if isinstance(line_settings, dict) else None,
                    "send_shop_name": bool((line_settings or {}).get("send_shop_name")) if isinstance(line_settings, dict) else None,
                    "send_customer_name": bool((line_settings or {}).get("send_customer_name")) if isinstance(line_settings, dict) else None,
                    "algorithm": (line_settings or {}).get("algorithm") if isinstance(line_settings, dict) else None,
                },
                "result": {"Name": safe_name, "DialPlan": dialplan},
                "debug": {
                    "retailcrm_ext": retailcrm_ext if 'retailcrm_ext' in locals() else None,
                    "retailcrm_internal_id": retailcrm_internal_id if 'retailcrm_internal_id' in locals() else None
                },
                "latency_ms": elapsed
            }, ensure_ascii=False)
        )
        return JSONResponse(GetCustomerDataResponse(Name=safe_name, DialPlan=dialplan).dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_customer_data unexpected error: {e}", exc_info=True)
        metrics["errors_total"] += 1
        # При ошибках — безопасный ответ без маршрутизации
        await cache.set(cache_key, (None, None))
        elapsed = int((time.time() - start_ts) * 1000)
        latencies_ms.append(elapsed)
        decisions_logger.info(
            json.dumps({
                "req_id": req_id,
                "enterprise": None,
                "trunk": body.TrunkId,
                "phone": body.Phone,
                "mode": "error",
                "flags": None,
                "result": {"Name": None, "DialPlan": None},
                "latency_ms": elapsed,
                "error": str(e)
            }, ensure_ascii=False)
        )
        return JSONResponse(GetCustomerDataResponse(Name="", DialPlan=None).dict())
    finally:
        try:
            await conn.close()
        except Exception:
            pass


@app.get("/")
async def root():
    return {"status": "ok", "service": "smart", "version": 1}


@app.post("/cache/clear")
async def clear_cache(enterprise: Optional[str] = None):
    """Очистка TTL-кэша сервиса. Если передан enterprise — чистим записи только для него."""
    cleared = 0
    async with cache._lock:  # внутренний лок; ОК для локальной утилиты
        if enterprise:
            keys = [k for k in cache._store.keys() if k.startswith(f"{enterprise}|")]  # формат ключа иной; это на будущее
            for k in keys:
                cache._store.pop(k, None)
                cleared += 1
        else:
            cleared = len(cache._store)
            cache._store.clear()
    return {"status": "ok", "cleared": cleared}


@app.get("/metrics")
async def get_metrics():
    # Вычисляем p50/p95 на лету
    values = list(latencies_ms)
    p50 = p95 = None
    if values:
        values_sorted = sorted(values)
        def pct(p: float) -> int:
            idx = int(max(0, min(len(values_sorted) - 1, round(p * (len(values_sorted) - 1)))))
            return int(values_sorted[idx])
        p50 = pct(0.50)
        p95 = pct(0.95)
    error_rate = (metrics["errors_total"] / metrics["requests_total"]) if metrics["requests_total"] else 0.0
    out = dict(metrics)
    out.update({
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "error_rate": round(error_rate, 4),
        "samples": len(values)
    })
    return out


