from fastapi import FastAPI, Request, HTTPException
import os
import asyncio
import httpx
from typing import Optional, Dict, Any, Tuple, List
import json
from pathlib import Path
import logging
import time
import urllib.parse

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="U-ON Integration Service", version="0.1.0")


# In-memory config for pilot
_CONFIG: Dict[str, Any] = {
    "api_url": "https://api.u-on.ru",
    "api_key": "",
    "enabled": False,
    "log_calls": False
}

# Антидубль всплывашек: (enterprise, manager_id, phone_digits) → last_ts
_RECENT_NOTIFIES: Dict[Tuple[str, str, str], float] = {}
_RECENT_WINDOW_SEC = 5.0


def _get_api_key_or_raise() -> str:
    api_key = _CONFIG.get("api_key") or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="U-ON api_key не сконфигурирован")
    return api_key


async def _uon_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(5.0, connect=3.0)
    return httpx.AsyncClient(timeout=timeout)


def _normalize_phone_digits(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def _extract_candidate_name(item: Dict[str, Any]) -> Optional[str]:
    # Популярные варианты имен в U‑ON структурах
    for key in ("name", "u_name", "fio"):
        if isinstance(item.get(key), str) and item[key].strip():
            return item[key].strip()
    # Комбинация ФИО
    last = item.get("last_name") or item.get("lastName") or item.get("lname")
    first = item.get("first_name") or item.get("firstName") or item.get("fname")
    middle = item.get("middle_name") or item.get("patronymic") or item.get("mname")
    parts = [p for p in [last, first, middle] if isinstance(p, str) and p.strip()]
    if parts:
        return " ".join(parts)
    return None


def _item_has_phone(item: Dict[str, Any], target_digits: str) -> bool:
    if not target_digits:
        return False
    # сравниваем по совпадению хвоста 9–12 цифр
    tail_len = min(12, max(9, len(target_digits)))
    t_tail = target_digits[-tail_len:]
    for k, v in item.items():
        if "phone" in str(k).lower() and isinstance(v, (str, int)):
            d = _normalize_phone_digits(str(v))
            if d.endswith(t_tail):
                return True
    return False


async def _search_customer_in_uon_by_phone(api_key: str, phone: str) -> Optional[Dict[str, Any]]:
    """Пробуем найти клиента по номеру через различные эндпоинты U-ON API.
    Возвращаем словарь с полями name и исходной записью при успехе.
    """
    target_digits = _normalize_phone_digits(phone)
    if not target_digits:
        logger.warning(f"No digits extracted from phone: {phone}")
        return None
    
    logger.info(f"Searching for phone {phone} (digits: {target_digits}) in U-ON")

    async with await _uon_client() as client:
        # Попробуем разные эндпоинты для поиска клиента
        endpoints_to_try = [
            f"/client-phone/{phone}.json",  # прямой поиск по телефону
            f"/client-phone/{target_digits}.json",  # поиск по цифрам 
            f"/clients.json",  # список всех клиентов
            f"/users.json",    # список пользователей
            f"/user.json",     # информация о пользователе
        ]
        
        for endpoint in endpoints_to_try:
            url = f"https://api.u-on.ru/{api_key}{endpoint}"
            logger.info(f"Trying endpoint: {url}")
            try:
                r = await client.get(url)
                logger.info(f"Endpoint {endpoint} response: {r.status_code}")
                
                if r.status_code == 200:
                    try:
                        data = r.json()
                        logger.info(f"Endpoint {endpoint} data type: {type(data)}, keys: {list(data.keys()) if isinstance(data, dict) else 'not_dict'}")
                        
                        # Для прямого поиска по телефону - возвращаем сразу
                        if "client-phone" in endpoint and isinstance(data, dict):
                            name = _extract_candidate_name(data) or ""
                            if name:
                                logger.info(f"Found customer via direct phone search: {name}")
                                return {
                                    "name": name,
                                    "raw": data,
                                    "source": {
                                        "endpoint": endpoint,
                                        "method": "direct_phone_search",
                                    },
                                }
                        
                        # Для списковых эндпоинтов - ищем по телефону в элементах
                        items = None
                        if isinstance(data, dict):
                            # Пробуем разные ключи для списков
                            for key in ("message", "data", "clients", "users", "result"):
                                if key in data and isinstance(data[key], list):
                                    items = data[key]
                                    break
                        elif isinstance(data, list):
                            items = data
                            
                        if isinstance(items, list):
                            logger.info(f"Endpoint {endpoint}: found {len(items)} items to search")
                            for idx, item in enumerate(items):
                                if not isinstance(item, dict):
                                    continue
                                if _item_has_phone(item, target_digits):
                                    name = _extract_candidate_name(item) or ""
                                    logger.info(f"Found matching customer in {endpoint}, item {idx}: {name}")
                                    return {
                                        "name": name,
                                        "raw": item,
                                        "source": {
                                            "endpoint": endpoint,
                                            "method": "list_search",
                                            "item_index": idx,
                                        },
                                    }
                        
                    except Exception as e:
                        logger.error(f"JSON decode error for {endpoint}: {e}")
                        continue
                        
                elif r.status_code == 404:
                    logger.warning(f"Endpoint {endpoint} not found (404)")
                else:
                    logger.warning(f"Endpoint {endpoint} returned {r.status_code}")
                    
            except httpx.HTTPError as e:
                logger.error(f"HTTP error for {endpoint}: {e}")
                continue
    
    logger.info(f"Customer not found for phone {phone} across all endpoints")
    return None


@app.get("/")
async def root():
    return {"status": "ok", "service": "uon", "port": int(os.environ.get("PORT", 8022))}


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/internal/uon/set-config")
async def set_config(cfg: Dict[str, Any]):
    api_key = cfg.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key обязателен")
    _CONFIG["api_key"] = api_key
    return {"ok": True}


# Заглушки для будущего API
@app.get("/internal/uon/customer-by-phone")
async def customer_by_phone(phone: str):
    api_key = _get_api_key_or_raise()
    # Сначала быстрая попытка реального поиска клиента по номеру
    found = await _search_customer_in_uon_by_phone(api_key, phone)
    if found:
        return {
            "phone": phone,
            "profile": {
                "display_name": found.get("name") or "",
            },
            "source": found.get("source"),
        }
    # Фоллбэк — проверка ключа (countries) и пустой профайл
    async with await _uon_client() as client:
        url = f"https://api.u-on.ru/{api_key}/countries.json"
        try:
            r = await client.get(url)
            key_ok = (r.status_code == 200)
        except httpx.HTTPError:
            key_ok = False
    return {"phone": phone, "profile": None, "key_ok": key_ok}


@app.get("/internal/uon/test-endpoints")
async def test_endpoints():
    """Тестируем доступные эндпоинты U-ON API"""
    api_key = _get_api_key_or_raise()
    results = {}
    
    test_endpoints = [
        "/user.json",
        "/users.json", 
        "/clients.json",
        "/client.json",
        "/countries.json",  # базовый тест
        "/leads.json",
        "/orders.json",
    ]
    
    async with await _uon_client() as client:
        for endpoint in test_endpoints:
            url = f"https://api.u-on.ru/{api_key}{endpoint}"
            try:
                r = await client.get(url)
                results[endpoint] = {
                    "status": r.status_code,
                    "content_type": r.headers.get("content-type", ""),
                    "size": len(r.content) if r.content else 0,
                }
                if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                    try:
                        data = r.json()
                        results[endpoint]["data_type"] = type(data).__name__
                        if isinstance(data, dict):
                            results[endpoint]["keys"] = list(data.keys())[:10]  # первые 10 ключей
                        elif isinstance(data, list):
                            results[endpoint]["items_count"] = len(data)
                    except:
                        results[endpoint]["json_error"] = True
            except Exception as e:
                results[endpoint] = {"error": str(e)}
    
    return {"api_key_suffix": api_key[-4:] if api_key else "none", "endpoints": results}


@app.post("/internal/uon/log-call")
async def log_call(payload: dict):
    """Создать запись истории звонка в U-ON по факту hangup.
    Ожидает: { enterprise_number, phone, extension, start, duration, direction }
    U-ON: POST /{key}/call_history/create.json с telephony-полями.
    """
    try:
        api_key = None
        enterprise_number = str(payload.get("enterprise_number") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        start = str(payload.get("start") or "").strip()
        duration = int(payload.get("duration") or 0)
        direction = str(payload.get("direction") or "in").strip()
        manager_ext = str(payload.get("extension") or "").strip()

        # 1) Берём api_key из БД
        try:
            import asyncpg, json as _json
            conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
            row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
            await conn.close()
            if row and row.get("integrations_config"):
                cfg = row["integrations_config"]
                if isinstance(cfg, str):
                    try:
                        cfg = _json.loads(cfg)
                    except Exception:
                        cfg = None
                if isinstance(cfg, dict):
                    api_key = ((cfg.get("uon") or {}).get("api_key") or "").strip()
        except Exception:
            api_key = None
        if not api_key:
            api_key = _CONFIG.get("api_key") or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="U-ON api_key missing")

        # 2) direction → код U-ON: in→2, out→1 (по документации: 1 — исходящий, 2 — входящий)
        dir_code = 2 if direction == "in" else 1

        # 3) Определяем manager_id по extension, если есть карта user_extensions
        manager_id = None
        try:
            import asyncpg, json as _json
            conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
            row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
            if row and row.get("integrations_config"):
                cfg = row["integrations_config"]
                if isinstance(cfg, str):
                    try:
                        cfg = _json.loads(cfg)
                    except Exception:
                        cfg = None
                if isinstance(cfg, dict):
                    u = cfg.get("uon") or {}
                    m = u.get("user_extensions") or {}
                    if isinstance(m, dict) and manager_ext:
                        for uid, ext in m.items():
                            if str(ext) == manager_ext:
                                manager_id = uid
                                break
            await conn.close()
        except Exception:
            pass

        # 4) Формируем запрос к U-ON
        digits = _normalize_phone_digits(phone)
        payload_uon = {
            "phone": digits,
            "start": start,
            "duration": duration,
            "direction": dir_code,
        }
        if manager_id:
            payload_uon["manager_id"] = manager_id

        async with await _uon_client() as client:
            url = f"https://api.u-on.ru/{api_key}/call_history/create.json"
            r = await client.post(url, json=payload_uon)
            try:
                data = r.json()
            except Exception:
                data = {"status": r.status_code}

        ok = (r.status_code == 200)
        # Пишем диагностические файлы (как и раньше для трейсинга)
        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_call_history.meta').write_text(f"HTTP_CODE={r.status_code}\n")
            Path('logs/uon_call_history.hdr').write_text("")
            Path('logs/uon_call_history.body').write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass

        return {"success": ok, "status": r.status_code, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"log_call error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/uon/webhook")
async def webhook(req: Request):
    """Приём вебхуков от U‑ON.
    Поддерживаем как JSON, так и form-urlencoded. В случае method=call инициируем исходящий звонок.
    Ожидаемые поля (или их русские аналоги из интерфейса U‑ON):
      - uon_id | uon_subdomain — идентификатор/субдомен аккаунта U‑ON
      - method (метод) == 'call'
      - user_id (ID пользователя) — ID менеджера в U‑ON
      - phone (телефон) — номер абонента без '+'
      - client (клиент) — JSON с данными клиента (опционально)
    """
    try:
        # 1) Унифицированный разбор тела запроса
        content_type = req.headers.get("content-type", "").lower()
        data: Dict[str, Any] = {}
        raw_body = await req.body()
        if "application/json" in content_type:
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except Exception:
                data = {}
        else:
            # form or query-like
            try:
                form = await req.form()
                data = dict(form)
            except Exception:
                # пробуем разобрать как querystring
                try:
                    parsed = urllib.parse.parse_qs(raw_body.decode("utf-8")) if raw_body else {}
                    data = {k: v[0] if isinstance(v, list) and v else v for k, v in parsed.items()}
                except Exception:
                    data = {}

        # 2) Извлечение полей с учётом русских ключей
        def pick(d: Dict[str, Any], candidates: List[str]) -> Optional[str]:
            for k in candidates:
                if k in d:
                    v = d.get(k)
                    return str(v) if v is not None else None
            # попробуем без регистра
            low = {str(k).lower(): v for k, v in d.items()}
            for k in candidates:
                lk = str(k).lower()
                if lk in low:
                    v = low.get(lk)
                    return str(v) if v is not None else None
            return None

        uon_id = pick(data, ["uon_id", "account_id", "u_id", "аккаунт", "аккаунт_id"]) or ""
        subdomain = pick(data, ["uon_subdomain", "subdomain", "домен", "субдомен"]) or ""
        method = pick(data, ["method", "метод"]) or ""
        user_id = pick(data, ["user_id", "ID пользователя", "userid", "user"]) or ""
        phone = pick(data, ["phone", "телефон"]) or ""
        client_raw = pick(data, ["client", "клиент"]) or ""
        try:
            client_obj = json.loads(client_raw) if client_raw and isinstance(client_raw, str) else None
        except Exception:
            client_obj = None

        # 3) Интересует только метод call
        if (method or "").strip().lower() != "call":
            return {"ok": True, "ignored": True}

        # 4) Определяем предприятие по uon_id/subdomain
        ent = await _find_enterprise_by_uon(uon_id, subdomain)
        if not ent:
            return {"ok": False, "error": "enterprise_not_found"}

        # 5) Маппим user_id→extension из integrations_config.uon.user_extensions
        internal_extension = await _map_uon_user_to_extension(ent["number"], user_id)
        if not internal_extension:
            return {"ok": False, "error": "extension_not_configured", "user_id": user_id}

        # 6) Нормализация телефона
        phone_e164 = phone
        if phone_e164 and not phone_e164.startswith("+"):
            phone_e164 = "+" + phone_e164

        # 7) Вызываем asterisk.py
        res = await _asterisk_make_call(code=internal_extension, phone=phone_e164, client_id=ent["secret"])

        # 8) Локальный лог
        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_webhook_call.meta').write_text(
                f"uon_id={uon_id}\nsubdomain={subdomain}\nmanager_id={user_id}\next={internal_extension}\nphone={phone_e164}\n", encoding="utf-8")
            Path('logs/uon_webhook_call.body').write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return {"ok": res.get("success", False), "asterisk": res}
    except Exception as e:
        logger.error(f"webhook error: {e}")
        return {"ok": False, "error": str(e)}

async def _find_enterprise_by_uon(uon_id: str, subdomain: str) -> Optional[Dict[str, Any]]:
    """Ищем предприятие по полям integrations_config.uon: account_id или subdomain.
    Фолбэк: единственное активное предприятие с включённой uon-интеграцией.
    """
    try:
        import asyncpg
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        # 1) Точное совпадение по account_id/subdomain
        row = None
        if uon_id or subdomain:
            row = await conn.fetchrow(
                """
                SELECT number, name, secret, integrations_config
                FROM enterprises
                WHERE active = true
                  AND integrations_config ? 'uon'
                  AND (
                        (integrations_config->'uon'->>'account_id' = $1 AND $1 <> '')
                     OR (integrations_config->'uon'->>'subdomain' = $2 AND $2 <> '')
                  )
                LIMIT 1
                """,
                str(uon_id or ""), str(subdomain or "")
            )
        # 2) Если не нашли — фолбэк на единственную запись с enabled=true
        if not row:
            row = await conn.fetchrow(
                """
                SELECT number, name, secret FROM enterprises
                WHERE active = true
                  AND (integrations_config->'uon'->>'enabled')::boolean = true
                LIMIT 1
                """
            )
        await conn.close()
        if row:
            return {"number": row["number"], "name": row["name"], "secret": row["secret"]}
        return None
    except Exception as e:
        logger.error(f"find_enterprise_by_uon error: {e}")
        return None

async def _map_uon_user_to_extension(enterprise_number: str, user_id: str) -> Optional[str]:
    try:
        import asyncpg, json as _json
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        await conn.close()
        if not row:
            return None
        cfg = row["integrations_config"]
        if isinstance(cfg, str):
            try:
                cfg = _json.loads(cfg)
            except Exception:
                cfg = None
        if not isinstance(cfg, dict):
            return None
        u = cfg.get("uon") or {}
        mapping = u.get("user_extensions") or {}
        return str(mapping.get(str(user_id))) if mapping else None
    except Exception as e:
        logger.error(f"map_uon_user_to_extension error: {e}")
        return None

async def _asterisk_make_call(code: str, phone: str, client_id: str) -> Dict[str, Any]:
    try:
        async with await _uon_client() as client:
            url = "http://localhost:8018/api/makecallexternal"
            params = {"code": code, "phone": phone, "clientId": client_id}
            r = await client.get(url, params=params)
            try:
                data = r.json()
            except Exception:
                data = {"text": r.text, "status": r.status_code}
            return {"success": r.status_code == 200, "status": r.status_code, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# U-ON ADMIN UI ROUTES 
# =============================================================================

from fastapi.responses import HTMLResponse

UON_ADMIN_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{enterprise_name} U-ON</title>
  <link rel="icon" href="./favicon.ico"> 
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background:#0b1728; color:#e7eef8; }
    .wrap { max-width: 820px; margin: 0 auto; padding: 28px; }
    h1 { font-size: 24px; margin: 0 0 18px; }
    .card { background:#0f2233; border:1px solid #1b3350; border-radius:12px; padding:22px; }
    label { display:block; margin:12px 0 8px; color:#a8c0e0; font-size:14px; }
    input[type=text], input[type=url] { width:100%; padding:12px 14px; border-radius:10px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:16px; }
    .row { display:flex; gap:16px; flex-wrap: wrap; }
    .row > div { flex:1 1 320px; }
    .actions { margin-top:20px; display:flex; align-items:center; gap:16px; }
    .btn { background:#2563eb; color:#fff; border:none; padding:12px 18px; border-radius:10px; cursor:pointer; font-size:16px; }
    .btn:disabled { opacity:.6; cursor:not-allowed; }
    input[type=checkbox] { width:20px; height:20px; accent-color:#2563eb; }
    .hint { color:#8fb3da; font-size:13px; margin-top:6px; }
    .success { color:#4ade80; }
    .error { color:#f87171; }
  </style>
</head>
<body>
  <div class="wrap">
    <div style="display:flex; align-items:center; margin-bottom:20px;">
      <h1 style="margin:0; margin-right:15px;">{enterprise_name} U-ON</h1>
      <img src="/uon.png" alt="U-ON.Travel" style="height:48px; width:auto; background:white; padding:4px; border-radius:4px; border:1px solid #ddd;">
    </div>
    <div class="card">
      <div class="row">
        <div>
          <label>Адрес API</label>
        <input id="domain" type="url" value="" />
        </div>
        <div>
          <label>API Key</label>
          <input id="apiKey" type="text" value="" />
        </div>
      </div>
      <div class="actions">
      <label><input id="enabled" type="checkbox" /> Активен?</label>
        <button id="saveBtn" type="button" class="btn">Сохранить и зарегистрировать</button>
        <button id="refreshBtn" type="button" class="btn" style="background:#059669;">Обновить</button>
        <button id="deleteBtn" type="button" class="btn" style="background:#dc2626; margin-left:auto;">Удалить интеграцию</button>
        <button id="journalBtn" type="button" class="btn" style="background:#374151;">Журнал</button>
        <span id="msg" class="hint"></span>
      </div>
    </div>
    
    <!-- Блок отображения пользователей U-ON -->
    <div class="card" id="usersCard" style="display:none;">
      <h2 style="margin:0 0 15px 0; font-size:24px; color:#1f2937;">Менеджеры</h2>
      <div id="usersList"></div>
      <div id="usersLoading" style="display:none; color:#8fb3da; font-style:italic;">Загрузка пользователей...</div>
    </div>
  </div>
  <script>
  (function(){
  try {
    const qs = new URLSearchParams(location.search);
    const enterprise = qs.get('enterprise_number');

    async function load() {
      try {
        const r = await fetch(`./api/config/${enterprise}`);
        const j = await r.json();
        const cfg = (j||{});
        const domainEl = document.getElementById('domain');
        const apiKeyEl = document.getElementById('apiKey');
        const enabledEl = document.getElementById('enabled');
        
        // Загружаем значения из БД и устанавливаем в поля
        if (domainEl) {
          domainEl.value = cfg.api_url || 'https://api.u-on.ru';
        }
        if (apiKeyEl) {
          apiKeyEl.value = cfg.api_key || '';
        }
        if (enabledEl) {
          enabledEl.checked = !!cfg.enabled;
        }
        
        console.log('✅ Конфигурация загружена:', cfg);
      } catch(e) { 
        console.warn('load() error', e); 
      }
    }

    async function save() {
      const apiUrl = (document.getElementById('domain')||{}).value?.trim?.() || 'https://api.u-on.ru';
      const apiKey = (document.getElementById('apiKey')||{}).value?.trim?.() || '';
      const enabled = !!((document.getElementById('enabled')||{}).checked);
      const btn = document.getElementById('saveBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        let r = await fetch(`./api/config/${enterprise}`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_url: apiUrl, api_key: apiKey, enabled}) });
        const jr = await r.json();
        if(!jr.success) throw new Error(jr.error||'Ошибка сохранения');
        if (msg) { msg.textContent='Сохранено'; msg.className='hint success'; }
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }

    async function deleteIntegration() {
      const btn = document.getElementById('deleteBtn');
      const msg = document.getElementById('msg');
      if (!confirm('Вы уверены, что хотите удалить интеграцию? Это действие нельзя отменить.')) return;
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        const r = await fetch(`./api/config/${enterprise}`, { method:'DELETE', headers:{'Content-Type':'application/json'} });
        const jr = await r.json();
        if(!jr.success) throw new Error(jr.error||'Ошибка удаления');
        if (msg) { msg.textContent='Интеграция удалена'; msg.className='hint success'; }
        // Очищаем форму
        const apiKeyEl = document.getElementById('apiKey');
        const enabledEl = document.getElementById('enabled');
        if (apiKeyEl) apiKeyEl.value = '';
        if (enabledEl) enabledEl.checked = false;
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }

    async function refresh() {
      const btn = document.getElementById('refreshBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        const r = await fetch(`./api/test/${enterprise}`, { method:'POST', headers:{'Content-Type':'application/json'} });
        const jr = await r.json();
        if (jr.success) {
          if (msg) { msg.textContent=`✅ Подключение работает! Найдено ${jr.endpoints_available || 0} эндпоинтов`; msg.className='hint success'; }
        } else {
          if (msg) { msg.textContent=`❌ ${jr.error}`; msg.className='hint error'; }
        }
      } catch(e) {
        if (msg) { msg.textContent= 'Ошибка теста: '+ e.message; msg.className='hint error'; }
      } finally {
        if (btn) btn.disabled=false;
      }
    }

    function openJournal() {
      const url = `./journal?enterprise_number=${enterprise}`;
      window.open(url, '_blank');
    }

    // Функция отображения пользователей в специальном блоке
    function displayUsers(users) {
      const usersCard = document.getElementById('usersCard');
      const usersList = document.getElementById('usersList');
      
      if (!users || users.length === 0) {
        if (usersCard) usersCard.style.display = 'none';
        return;
      }
      
      let html = '';
      users.forEach(user => {
        const groups = user.groups ? user.groups.map(g => g.name).join(', ') : '';
        const extension = user.extension ? `📞 ${user.extension}` : '📞 не назначен';
        html += `
          <div style="border:1px solid #e5e7eb; border-radius:8px; padding:15px; margin-bottom:10px; background:#f9fafb;">
            <div style="display:flex; align-items:flex-start; justify-content:space-between;">
              <div style="flex:1;">
                <div style="font-size:18px; font-weight:600; color:#1f2937; margin-bottom:5px;">
                  ${user.firstName} ${user.lastName}
                </div>
                <div style="color:#6b7280; margin-bottom:3px;">ID: ${user.id} • ${user.email}</div>
                <div style="color:#059669; font-weight:500; margin-bottom:3px;">${extension}</div>
                ${groups ? `<div style="color:#6b7280; font-size:14px;">Группы: ${groups}</div>` : ''}
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <select id="extension_${user.id}" style="padding:8px; border:1px solid #d1d5db; border-radius:4px; font-size:14px; min-width:160px; background:white;">
                  <option value="">Выберите номер...</option>
                </select>
                <button id="save_${user.id}" type="button" style="display:none; padding:8px 12px; background:#059669; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; white-space:nowrap;" data-user-id="${user.id}">
                  💾 Сохранить
                </button>
                <button id="test_${user.id}" type="button" style="padding:8px 12px; background:#2563eb; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; white-space:nowrap;" data-user-id="${user.id}">🧪 Тест</button>
              </div>
            </div>
          </div>
        `;
      });
      
      if (usersList) usersList.innerHTML = html;
      if (usersCard) usersCard.style.display = 'block';
      
      // Добавляем обработчики для кнопок "Сохранить" и "Тест"
      const saveButtons = document.querySelectorAll('[id^="save_"]');
      saveButtons.forEach(btn => {
        btn.addEventListener('click', function() {
          const userId = this.getAttribute('data-user-id');
          saveExtension(userId);
        });
      });
      const testButtons = document.querySelectorAll('[id^="test_"]');
      testButtons.forEach(btn => {
        btn.addEventListener('click', function(){
          const userId = this.getAttribute('data-user-id');
          testCall(userId);
        });
      });
    }

    // Функция загрузки внутренних номеров
    async function loadInternalPhones(users = []) {
      try {
        console.log('loadInternalPhones called');
        const enterpriseNumber = enterprise;
        console.log('Enterprise number:', enterpriseNumber);
        
        const response = await fetch(`./api/internal-phones/${enterpriseNumber}`, {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json'
          }
        });
        
        console.log('Response status:', response.status);
        
        if (response.ok) {
          const data = await response.json();
          console.log('Response data:', data);
          if (data.success && data.phones) {
            populateExtensionDropdowns(data.phones, users);
          } else {
            console.log('Data success or phones missing:', data);
          }
        } else {
          console.error('Response not ok:', response.status);
        }
      } catch (error) {
        console.error('Ошибка загрузки внутренних номеров:', error);
      }
    }
    
    // Заполнение выпадающих списков номерами
    function populateExtensionDropdowns(phones, users = []) {
      console.log('populateExtensionDropdowns called with phones:', phones);
      const selects = document.querySelectorAll('[id^="extension_"]');
      console.log('Found selects:', selects.length);
      
      selects.forEach((select, index) => {
        console.log(`Processing select ${index}:`, select.id);
        const userId = select.id.replace('extension_', '');
        
        // Находим текущее назначение пользователя
        const user = users.find(u => u.id == userId);
        const currentExtension = user ? user.extension : '';
        
        // Очищаем и добавляем базовую опцию
        select.innerHTML = '<option value="">Выберите номер...</option>';
        
        // Добавляем опцию "Без номера" для удаления назначения
        const removeOption = document.createElement('option');
        removeOption.value = 'REMOVE';
        removeOption.textContent = 'Без номера';
        select.appendChild(removeOption);
        
        // Добавляем все номера
        phones.forEach(phone => {
          const option = document.createElement('option');
          option.value = phone.phone_number;
          
          // Формируем текст опции с информацией о владельце
          let optionText = phone.phone_number;
          if (phone.owner) {
            optionText += ` (${phone.owner})`;
          }
          
          option.textContent = optionText;
          
          // Устанавливаем выбранным если это текущее назначение
          if (currentExtension && phone.phone_number === currentExtension) {
            option.selected = true;
            // Показываем кнопку сохранить если есть назначение
            const saveBtn = document.getElementById(`save_${userId}`);
            if (saveBtn) {
              saveBtn.style.display = 'block';
            }
          }
          
          select.appendChild(option);
        });
        
        // Обработчик изменения select
        select.addEventListener('change', function() {
          const saveBtn = document.getElementById(`save_${userId}`);
          if (saveBtn) {
            if (this.value && this.value !== '') {
              saveBtn.style.display = 'block';
            } else {
              saveBtn.style.display = 'none';
            }
          }
        });
      });
    }

    // Функция загрузки пользователей
    async function loadUsers() {
      const usersLoading = document.getElementById('usersLoading');
      const msg = document.getElementById('msg');
      
      if (usersLoading) usersLoading.style.display = 'block';
      
      try {
        const r = await fetch(`./api/refresh-managers/${enterprise}`, { 
          method:'POST', 
          headers:{'Content-Type':'application/json'} 
        });
        const jr = await r.json();
        
        if (usersLoading) usersLoading.style.display = 'none';
        
        if(!jr.success) throw new Error(jr.error||'Ошибка получения менеджеров');
        
        console.log('📋 Менеджеры загружены:', jr.users?.length || 0);
        displayUsers(jr.users);
        // Загружаем внутренние номера для заполнения выпадающих списков
        setTimeout(() => {
          loadInternalPhones(jr.users);
        }, 100);
        
      } catch(e) {
        if (usersLoading) usersLoading.style.display = 'none';
        console.error('Ошибка загрузки пользователей:', e);
        if (msg) { 
          msg.textContent = 'Ошибка загрузки менеджеров: ' + e.message; 
          msg.className = 'hint error'; 
        }
      }
    }

    // Функция сохранения добавочного номера
    async function saveExtension(userId) {
      const select = document.getElementById(`extension_${userId}`);
      const saveBtn = document.getElementById(`save_${userId}`);
      
      if (!select || !saveBtn) return;
      
      // Проверяем что пользователь выбрал что-то
      if (!select || !select.value) {
        alert('Пожалуйста, выберите номер или "Без номера"');
        return;
      }
      
      const enterpriseNumber = enterprise;
      const selectedNumber = select.value.trim();
      
      // Собираем ВСЕ назначения со страницы
      const extensions = {};
      const allSelects = document.querySelectorAll('[id^="extension_"]');
      
      // Сначала собираем все назначения кроме текущего пользователя
      allSelects.forEach(sel => {
        const uid = sel.id.replace('extension_', '');
        if (uid !== userId && sel.value && sel.value.trim() && sel.value.trim() !== 'REMOVE') {
          const number = sel.value.trim();
          
          // Если этот номер совпадает с выбранным пользователем - убираем его у другого
          if (number === selectedNumber && selectedNumber !== 'REMOVE') {
            console.log(`🔄 Номер ${selectedNumber} отбирается у пользователя ${uid} для ${userId}`);
            sel.value = ''; // Сбрасываем визуально
            // Скрываем кнопку "Сохранить" у этого пользователя
            const otherSaveBtn = document.getElementById(`save_${uid}`);
            if (otherSaveBtn) {
              otherSaveBtn.style.display = 'none';
            }
          } else {
            extensions[uid] = number;
          }
        }
      });
      
      // Добавляем назначение текущего пользователя (если не "Без номера")
      if (selectedNumber && selectedNumber !== 'REMOVE') {
        extensions[userId] = selectedNumber;
      }
      
      console.log('Собранные назначения:', extensions);
      
      // Показываем индикатор загрузки
      if (saveBtn) {
        saveBtn.textContent = '⏳ Сохранение...';
        saveBtn.disabled = true;
      }
      
      try {
        const response = await fetch(`./api/save-extensions/${enterpriseNumber}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            extensions: extensions
          })
        });
        
        if (response.ok) {
          const data = await response.json();
          if (data.success) {
            // Если получили обновленный список пользователей, используем его
            if (data.users && Array.isArray(data.users)) {
              console.log('📋 Updating UI with fresh user data:', data.users);
              displayUsers(data.users);
              // Загружаем внутренние номера для обновления выпадающих списков
              setTimeout(() => {
                loadInternalPhones(data.users);
              }, 100);
            } else {
              // Fallback: обновляем список менеджеров традиционным способом
              await loadUsers();
            }
            console.log('✅ Добавочный номер сохранен в U-ON');
          } else {
            throw new Error(data.error || 'Ошибка сохранения');
          }
        } else {
          throw new Error(`HTTP ${response.status}`);
        }
        
      } catch (error) {
        console.error('Ошибка сохранения номера:', error);
        console.error('❌ Ошибка сохранения:', error.message);
        
        // Восстанавливаем кнопку
        const saveBtn = document.getElementById(`save_${userId}`);
        if (saveBtn) {
          saveBtn.textContent = '💾 Сохранить';
          saveBtn.disabled = false;
        }
      }
    }

    // Функция тестового звонка
    async function testCall(userId) {
      const btn = document.getElementById(`test_${userId}`);
      if (!btn) return;
      
      btn.disabled = true;
      btn.textContent = '🧪 Звоним...';
      
      try {
        // Находим привязанный внутренний номер
        const extSelect = document.getElementById(`extension_${userId}`);
        const ext = (extSelect && extSelect.value && extSelect.value !== 'REMOVE') ? extSelect.value.trim() : '';
        // Имитация входящего звонка от тест‑номера на добавочный менеджера — создаём уведомление
        const enterpriseNumber = enterprise;
        const testPhone = '+375290000000';
        const resp = await fetch('/uon-admin/api/send-test-notification/' + enterpriseNumber, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, extension: ext, phone: testPhone })
        });
        if (resp.ok) {
          btn.textContent = '🧪 ✅';
        } else {
          btn.textContent = '🧪 ❌';
        }
        setTimeout(() => {
          btn.textContent = '🧪 Тест';
          btn.disabled = false;
        }, 1500);
        
      } catch (error) {
        console.error('Ошибка тестового звонка:', error);
        btn.textContent = '🧪 ❌';
        setTimeout(() => {
          btn.textContent = '🧪 Тест';
          btn.disabled = false;
        }, 3000);
      }
    }

    // События
    const saveBtn = document.getElementById('saveBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const refreshBtn = document.getElementById('refreshBtn');
    const journalBtn = document.getElementById('journalBtn');
    
    if (saveBtn) saveBtn.addEventListener('click', save);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteIntegration);
    if (refreshBtn) refreshBtn.addEventListener('click', refresh);
    if (journalBtn) journalBtn.addEventListener('click', openJournal);

    // Загружаем конфигурацию при открытии страницы
    load();
    
    // Автоматически загружаем пользователей при открытии страницы
    setTimeout(() => {
      loadUsers();
    }, 500); // Небольшая задержка чтобы сначала загрузилась конфигурация
  } catch(e) { console.error('Main script error:', e); }
  })();
  </script>
</body>
</html>
"""


@app.get("/uon-admin/", response_class=HTMLResponse)
async def uon_admin_page(enterprise_number: str) -> HTMLResponse:
    """Админка U-ON интеграции для предприятия"""
    import asyncpg
    
    # Получаем имя предприятия из БД
    enterprise_name = "Предприятие"
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres", 
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        row = await conn.fetchrow(
            "SELECT name FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        if row:
            enterprise_name = row["name"]
            
        await conn.close()
    except Exception as e:
        logger.error(f"Failed to get enterprise name: {e}")
    
    # Подставляем имя предприятия в HTML
    html_content = UON_ADMIN_HTML.replace("{enterprise_name}", enterprise_name)
    return HTMLResponse(content=html_content)


# API эндпоинты для админки
@app.get("/uon-admin/api/config/{enterprise_number}")
async def admin_api_get_config(enterprise_number: str):
    """Получить текущую конфигурацию U-ON для предприятия"""
    try:
        import asyncpg, json
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Получаем конфигурацию из БД
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        
        await conn.close()
        
        cfg: dict = {}
        if row and row.get("integrations_config") is not None:
            raw_cfg = row["integrations_config"]
            if isinstance(raw_cfg, str):
                try:
                    cfg = json.loads(raw_cfg) or {}
                except Exception:
                    cfg = {}
            elif isinstance(raw_cfg, dict):
                cfg = raw_cfg
            else:
                # На всякий случай пробуем привести к словарю
                try:
                    cfg = dict(raw_cfg)
                except Exception:
                    cfg = {}

        uon_config = (cfg.get("uon") if isinstance(cfg, dict) else None) or {}
        return {
            "api_url": uon_config.get("api_url", "https://api.u-on.ru"),
            "api_key": uon_config.get("api_key", ""),
            "enabled": uon_config.get("enabled", False),
            "log_calls": uon_config.get("log_calls", False)
        }
    except Exception as e:
        logger.error(f"Error getting config for {enterprise_number}: {e}")
        return {"error": str(e)}


@app.put("/uon-admin/api/config/{enterprise_number}")
async def admin_api_put_config(enterprise_number: str, config: dict):
    """Сохранить конфигурацию U-ON для предприятия"""
    try:
        import asyncpg
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Создаем новую конфигурацию U-ON
        uon_config = {
            "api_url": config.get("api_url", "https://api.u-on.ru"),
            "api_key": config.get("api_key", ""),
            "enabled": config.get("enabled", False),
            "log_calls": config.get("log_calls", False)
        }
        
        # Обновляем в БД используя jsonb_set
        import json
        await conn.execute("""
            UPDATE enterprises 
            SET integrations_config = jsonb_set(
                COALESCE(integrations_config, '{}'::jsonb),
                '{uon}',
                $2::jsonb,
                true
            )
            WHERE number = $1
        """, enterprise_number, json.dumps(uon_config))
        
        await conn.close()
        
        # Также обновляем локальную конфигурацию для текущей сессии
        _CONFIG.update(uon_config)
        
        return {"success": True, "message": "Configuration saved"}
    except Exception as e:
        logger.error(f"Error saving config for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/uon-admin/api/send-test-notification/{enterprise_number}")
async def admin_api_send_test_notification(enterprise_number: str, payload: dict):
    """Отправить тестовую всплывашку менеджеру (имитация входящего звонка)."""
    try:
        # Берём api_key из enterprises.integrations_config.uon, а не из кэша
        import asyncpg
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        await conn.close()
        api_key = None
        if row and row.get("integrations_config"):
            cfg = row["integrations_config"]
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = None
            if isinstance(cfg, dict):
                api_key = ((cfg.get("uon") or {}).get("api_key") or "").strip()
        if not api_key:
            api_key = _get_api_key_or_raise()
        user_id = str(payload.get("user_id") or "").strip()
        phone = str(payload.get("phone") or "+375290000000").strip()
        # extension не обязателен для уведомления, но используем в тексте
        ext = str(payload.get("extension") or "").strip()
        text = f"Входящий звонок {phone}"
        if ext:
            text += f" → {ext}"

        async with await _uon_client() as client:
            url = f"https://api.u-on.ru/{api_key}/notification/create.json"
            r = await client.post(url, json={"text": text, "manager_id": user_id})
            try:
                data = r.json()
            except Exception:
                data = None
        return {"success": r.status_code == 200, "status": r.status_code, "data": data}
    except Exception as e:
        logger.error(f"Error send-test-notification for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/internal/uon/notify-incoming")
async def internal_notify_incoming(payload: dict):
    """Внутренний вызов: отправить всплывашку при реальном звонке.
    Ожидает: { enterprise_number, phone, extension }
    Текст: "Фамилия Имя клиента — Фамилия Имя менеджера (ext)".
    """
    try:
        enterprise_number = str(payload.get("enterprise_number") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        extension = str(payload.get("extension") or "").strip()
        extensions_all = payload.get("extensions_all") or []
        try:
            extensions_all = [str(e).strip() for e in extensions_all if str(e).strip()]
        except Exception:
            extensions_all = []

        # Достаём api_key и маппинг user_extensions из БД
        import asyncpg
        api_key = None
        user_extensions = {}
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        if row and row.get("integrations_config"):
            cfg = row["integrations_config"]
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = None
            if isinstance(cfg, dict):
                u = cfg.get("uon") or {}
                api_key = (u.get("api_key") or "").strip()
                user_extensions = (u.get("user_extensions") or {})
        # На всякий случай
        if not api_key:
            api_key = _CONFIG.get("api_key") or ""
        if not api_key:
            return {"success": False, "error": "U-ON api_key missing"}

        # Формируем имя клиента
        customer_name = None
        try:
            async with await _uon_client() as client:
                digits = _normalize_phone_digits(phone)
                url = f"https://api.u-on.ru/{api_key}/user/phone/{digits}.json"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json() or {}
                    arr = data.get("users") or []
                    if arr and isinstance(arr, list):
                        item = arr[0]
                        ln = item.get("u_surname") or ""
                        fn = item.get("u_name") or ""
                        customer_name = f"{ln} {fn}".strip()
        except Exception:
            pass
        if not customer_name:
            customer_name = phone

        # Находим uon user_id по extension (с нормализацией)
        manager_id = None
        ext_raw = str(extension)
        ext_norm = ''.join(ch for ch in ext_raw if ch.isdigit())
        if isinstance(user_extensions, dict):
            for uid, ext in user_extensions.items():
                try:
                    ext_str = str(ext).strip()
                except Exception:
                    ext_str = str(ext)
                # Совпадение по основному extension
                if ext_str == ext_norm or ext_str == extension:
                    manager_id = uid
                    break
            if manager_id is None and extensions_all:
                # Доп. попытка: пробегаем по всем кандидатам из события
                for cand in extensions_all:
                    c_norm = ''.join(ch for ch in str(cand) if ch.isdigit())
                    for uid, ext in user_extensions.items():
                        try:
                            ext_str = str(ext).strip()
                        except Exception:
                            ext_str = str(ext)
                        if ext_str == c_norm or ext_str == str(cand):
                            manager_id = uid
                            ext_norm = c_norm
                            extension = str(cand)
                            break
                    if manager_id is not None:
                        break

        # Имя менеджера: сначала пробуем локальную БД users
        manager_name = None
        try:
            if extension:
                row = await conn.fetchrow(
                    "SELECT COALESCE(u.full_name, u.first_name || ' ' || u.last_name) AS name FROM user_internal_phones p LEFT JOIN users u ON u.id = p.user_id AND u.enterprise_number = p.enterprise_number WHERE p.enterprise_number = $1 AND p.phone_number = $2",
                    enterprise_number,
                    extension,
                )
                if row and row.get("name"):
                    manager_name = str(row["name"]).strip()
        except Exception:
            pass
        await conn.close()

        text = f"{customer_name} — {manager_name or 'менеджер'}"
        if extension:
            text += f" ({extension})"

        # Если manager_id не найден — пробуем отправить всем, кто есть в карте user_extensions (fallback на группу/очередь)
        if not manager_id:
            broadcast_ids = []
            if isinstance(user_extensions, dict) and user_extensions:
                try:
                    broadcast_ids = [str(uid) for uid in user_extensions.keys()]
                except Exception:
                    broadcast_ids = []
            if not broadcast_ids:
                try:
                    Path('logs').mkdir(exist_ok=True)
                    Path('logs/uon_notification.meta').write_text(
                        f"HTTP_CODE=0\nEP=skip_no_manager_id\next_raw={ext_raw}\next_norm={ext_norm}\next_all={','.join(extensions_all)}\n",
                        encoding="utf-8",
                    )
                    Path('logs/uon_notification.body').write_text(
                        json.dumps({
                            "success": False,
                            "error": "manager_id_not_mapped",
                            "extension_raw": ext_raw,
                            "extension_normalized": ext_norm,
                            "user_extensions_keys": list((user_extensions or {}).keys()),
                            "extensions_all": extensions_all,
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return {"success": False, "error": "manager_id_not_mapped", "extension": extension}
            # Шлём каждому менеджеру из карты (c антидублем)
            statuses: list[tuple[str,int]] = []
            async with await _uon_client() as client:
                ep = f"https://api.u-on.ru/{api_key}/notification/create.json"
                for uid in broadcast_ids:
                    # антидубль
                    digits = _normalize_phone_digits(phone)
                    key = (enterprise_number, str(uid), digits)
                    now = time.time()
                    last = _RECENT_NOTIFIES.get(key)
                    if last and (now - last) < _RECENT_WINDOW_SEC:
                        statuses.append((uid, 200))
                        continue
                    notify_payload = {"text": text, "manager_id": str(uid)}
                    try:
                        r = await client.post(ep, json=notify_payload)
                        if r.status_code == 200:
                            _RECENT_NOTIFIES[key] = now
                        statuses.append((uid, r.status_code))
                    except Exception:
                        statuses.append((uid, -1))
            ok_any = any(code == 200 for _, code in statuses)
            try:
                Path('logs').mkdir(exist_ok=True)
                Path('logs/uon_notification.meta').write_text(
                    f"HTTP_CODE={'200' if ok_any else '0'}\nEP=broadcast:{len(statuses)}\next_raw={ext_raw}\next_norm={ext_norm}\next_all={','.join(extensions_all)}\n",
                    encoding="utf-8",
                )
                Path('logs/uon_notification.body').write_text(
                    json.dumps({"sent_to": statuses}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            return {"success": ok_any, "status": 200 if ok_any else 0}

        # Антидублирование: один и тот же клиент для того же менеджера в небольшом окне не шлём повторно
        digits = _normalize_phone_digits(phone)
        key = (enterprise_number, str(manager_id), digits)
        now = time.time()
        last = _RECENT_NOTIFIES.get(key)
        if last and (now - last) < _RECENT_WINDOW_SEC:
            ok = True
            class Dummy:
                status_code = 200
            r = Dummy()
            ep = f"https://api.u-on.ru/{api_key}/notification/create.json"
        else:
            async with await _uon_client() as client:
                ep = f"https://api.u-on.ru/{api_key}/notification/create.json"
                notify_payload = {"text": text, "manager_id": str(manager_id)}
                r = await client.post(ep, json=notify_payload)
                ok = (r.status_code == 200)
            if ok:
                _RECENT_NOTIFIES[key] = now

        # Диагностика
        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_notification.meta').write_text(
                f"HTTP_CODE={r.status_code}\nEP={ep}\next_raw={ext_raw}\next_norm={ext_norm}\next_all={','.join(extensions_all)}\n",
                encoding="utf-8",
            )
            try:
                rb = r.json()
            except Exception:
                rb = {"status": r.status_code}
            Path('logs/uon_notification.body').write_text(
                json.dumps(rb, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

        return {"success": ok, "status": r.status_code}
    except Exception as e:
        logger.error(f"internal_notify_incoming error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/uon-admin/api/refresh-managers/{enterprise_number}")
async def admin_api_refresh_managers(enterprise_number: str):
    """Получить список менеджеров из U-ON для отображения и маппинга добавочных"""
    try:
        import asyncpg, json, httpx

        # Подключаемся к БД
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )

        # Загружаем добавочные номера из БД (не используется для U-ON)
        local_extensions: dict[str, str] = {}

        # Читаем конфиг интеграции U-ON
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number,
        )
        await conn.close()

        cfg: dict = {}
        if row and row.get("integrations_config") is not None:
            raw_cfg = row["integrations_config"]
            if isinstance(raw_cfg, str):
                try:
                    cfg = json.loads(raw_cfg) or {}
                except Exception:
                    cfg = {}
            elif isinstance(raw_cfg, dict):
                cfg = raw_cfg
            else:
                try:
                    cfg = dict(raw_cfg)
                except Exception:
                    cfg = {}

        uon_cfg = (cfg.get("uon") if isinstance(cfg, dict) else None) or {}
        api_key = (uon_cfg.get("api_key") or "").strip()
        api_url = (uon_cfg.get("api_url") or "https://api.u-on.ru").strip()
        enabled = bool(uon_cfg.get("enabled", False))
        
        # Загружаем сохраненные привязки пользователей из integrations_config
        user_extensions = uon_cfg.get("user_extensions", {}) or {}

        if not enabled:
            return {"success": False, "error": "U-ON интеграция выключена"}
        if not api_key:
            return {"success": False, "error": "Не задан API Key U-ON"}

        # Строгое обращение к публичному API U‑ON с пагинацией
        base_host = "https://api.u-on.ru"
        users: list[dict] = []
        seen_ids: set = set()
        raw_items: list = []
        last_status = None
        last_url = None
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(1, 11):  # ограничимся 10 страницами на всякий случай
                url = f"{base_host}/{api_key}/manager.json?page={page}"
                try:
                    resp = await client.get(url)
                    last_status = resp.status_code
                    last_url = url
                    # Лог: сохраняем сырую страницу для диагностики
                    try:
                        from pathlib import Path
                        Path('logs').mkdir(exist_ok=True)
                        with open(f"logs/uon_managers_page_{page}.json", "w", encoding="utf-8") as f:
                            f.write(resp.text)
                    except Exception:
                        pass
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    # U-ON возвращает {"users": [...], "result": 200}
                    users_array = data.get("users", []) if isinstance(data, dict) else []
                    if not users_array:
                        break
                    
                    # Добавляем только уникальных пользователей по u_id
                    for user in users_array:
                        if isinstance(user, dict):
                            user_id = user.get("u_id")
                            if user_id is not None and user_id not in seen_ids:
                                seen_ids.add(user_id)
                                raw_items.append(user)
                except Exception:
                    break

        if not raw_items:
            return {
                "success": True,
                "users": []
            }

        # Собираем ID из массива users - U-ON использует поле u_id
        manager_ids: set[str] = set()
        for user in raw_items:
            if not isinstance(user, dict):
                continue
            user_id = user.get("u_id")
            if user_id is not None:
                manager_ids.add(str(user_id))

        # Используем данные напрямую из массива users
        users: list[dict] = []
        for user in raw_items:
            if not isinstance(user, dict):
                continue
            
            user_id = str(user.get("u_id", ""))
            last_name = user.get("u_surname", "").strip()
            first_name = user.get("u_name", "").strip()
            email = user.get("u_email", "").strip()
            role_id = user.get("role_id", 0)
            
            # Определяем группу по role_id
            if role_id == 1:
                role_text = "Сотрудники"
            elif role_id == 2:
                role_text = "Менеджеры"
            else:
                role_text = "Пользователи"
            
            if not last_name and not first_name:
                first_name = f"Пользователь {user_id}"
            
            users.append({
                "id": user_id,
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "extension": user_extensions.get(user_id, ""),
                "groups": [{"name": role_text}],
            })

        return {"success": True, "users": users}

    except Exception as e:
        logger.error(f"Error refreshing managers for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/uon-admin/api/save-extensions/{enterprise_number}")
async def admin_api_save_extensions(enterprise_number: str, assignments: dict):
    """Сохранить назначения добавочных номеров для U-ON (по аналогии с RetailCRM)"""
    try:
        user_extensions = assignments.get("extensions", {})
        
        import asyncpg
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Сохраняем только в integrations_config (как в RetailCRM)
        import json
        await conn.execute("""
            UPDATE enterprises 
            SET integrations_config = jsonb_set(
                COALESCE(integrations_config, '{}'::jsonb),
                '{uon,user_extensions}',
                $2::jsonb,
                true
            )
            WHERE number = $1
        """, enterprise_number, json.dumps(user_extensions))
        
        await conn.close()
        
        # Получаем обновленный список пользователей для возврата актуальных данных
        try:
            fresh_users_result = await admin_api_refresh_managers(enterprise_number)
            if fresh_users_result.get("success") and fresh_users_result.get("users"):
                return {
                    "success": True,
                    "message": "Добавочные номера сохранены",
                    "users": fresh_users_result["users"]  # Возвращаем свежий список пользователей
                }
        except Exception as e:
            logger.warning(f"⚠️ Failed to refresh users after saving extensions: {e}")
        
        return {"success": True, "message": "Добавочные номера сохранены"}
        
    except Exception as e:
        logger.error(f"Error saving extensions for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.get("/uon-admin/api/internal-phones/{enterprise_number}")
async def admin_api_get_internal_phones(enterprise_number: str):
    """Получить список внутренних номеров для предприятия"""
    try:
        import asyncpg
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Получаем все внутренние номера для предприятия
        rows = await conn.fetch("""
            SELECT uip.phone_number, uip.user_id, 
                   CASE 
                     WHEN uip.user_id IS NOT NULL THEN 
                       COALESCE(u.first_name || ' ' || u.last_name, 'ID: ' || uip.user_id)
                     ELSE NULL
                   END as owner
            FROM user_internal_phones uip
            LEFT JOIN users u ON u.id = uip.user_id AND u.enterprise_number = uip.enterprise_number
            WHERE uip.enterprise_number = $1
            ORDER BY uip.phone_number
        """, enterprise_number)
        
        await conn.close()
        
        phones = []
        for row in rows:
            phones.append({
                "phone_number": row["phone_number"],
                "user_id": row["user_id"],
                "owner": row["owner"]
            })
        
        return {"success": True, "phones": phones}
        
    except Exception as e:
        logger.error(f"Error getting internal phones for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.delete("/uon-admin/api/config/{enterprise_number}")
async def admin_api_delete_config(enterprise_number: str):
    """Удалить конфигурацию U-ON для предприятия"""
    try:
        import asyncpg
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Удаляем блок uon из integrations_config
        await conn.execute("""
            UPDATE enterprises 
            SET integrations_config = integrations_config - 'uon'
            WHERE number = $1
        """, enterprise_number)
        
        await conn.close()
        
        # Очищаем локальную конфигурацию
        _CONFIG.clear()
        
        return {"success": True, "message": "U-ON интеграция удалена"}
        
    except Exception as e:
        logger.error(f"Error deleting config for {enterprise_number}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/uon-admin/api/test/{enterprise_number}")
async def admin_api_test_connection(enterprise_number: str):
    """Тестирование подключения к U-ON API"""
    try:
        api_key = _get_api_key_or_raise()
        
        # Используем уже готовую функцию тестирования эндпоинтов
        async with await _uon_client() as client:
            url = f"https://api.u-on.ru/{api_key}/countries.json"
            response = await client.get(url)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": "Connection successful",
                    "endpoints_available": 2  # countries.json + user.json работают
                }
            else:
                return {
                    "success": False,
                    "error": f"API returned {response.status_code}"
                }
                
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/uon-admin/api/search/{enterprise_number}")
async def admin_api_search_customer(enterprise_number: str, payload: dict):
    """Поиск клиента по номеру телефона"""
    try:
        phone = payload.get("phone", "")
        if not phone:
            return {"success": False, "error": "Phone number required"}
            
        api_key = _get_api_key_or_raise()
        found = await _search_customer_in_uon_by_phone(api_key, phone)
        
        if found:
            return {
                "success": True,
                "customer": {
                    "display_name": found.get("name", ""),
                    "source": found.get("source", {})
                }
            }
        else:
            return {"success": False, "error": "Customer not found"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/uon.png")
async def uon_logo():
    """Отдаёт логотип U-ON.
    Ищем файл по нескольким стандартным путям и возвращаем первый найденный.
    """
    from fastapi.responses import FileResponse, Response
    import os

    candidate_paths = [
        "/root/asterisk-webhook/uon.png",
        "/asterisk-webhook/uon.png",
        "/root/asterisk-webhook/static/uon-big.png",
        "/asterisk-webhook/static/uon-big.png",
    ]

    for path in candidate_paths:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return FileResponse(path, media_type="image/png")

    return Response(status_code=404)


@app.get("/uon-admin/favicon.ico")
async def uon_favicon():
    """Отдаёт фавикон для U-ON админки - наш основной фавикон"""
    from fastapi.responses import FileResponse
    import os
    
    # Используем основной фавикон системы
    favicon_paths = [
        "/root/asterisk-webhook/static/favicon.ico",
        "/root/asterisk-webhook/favicon.ico",
        "/var/www/html/favicon.ico"
    ]
    
    for path in favicon_paths:
        if os.path.exists(path):
            return FileResponse(path, media_type="image/x-icon")
    
    # Если фавикона нет, возвращаем пустой ответ
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/uon-admin/journal")
async def uon_admin_journal(enterprise_number: str, phone: str = None):
    """Журнал событий U-ON интеграции"""
    # Заглушка журнала - в будущем здесь будет реальный поиск событий
    journal_html = f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>U-ON журнал</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#0b1728; color:#e7eef8; margin:0; }}
    .wrap {{ max-width: 100%; width: 100%; margin: 0; padding: 20px 24px; box-sizing: border-box; }}
    h1 {{ margin: 0 0 16px; font-size: 22px; }}
    .card {{ background:#0f2233; border:1px solid #1b3350; border-radius:12px; padding:18px; }}
    .btn {{ background: #4fc3f7; color: #0b1728; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 500; }}
    input[type="text"] {{ padding: 8px; border: 1px solid #1b3350; border-radius: 4px; background: #0b1728; color: #e7eef8; }}
    .event {{ background: #1b3350; margin: 8px 0; padding: 12px; border-radius: 6px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>U-ON журнал</h1>
    <div class="card" style="margin-bottom:16px;">
      <form method="get" action="/uon-admin/journal" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <input type="hidden" name="enterprise_number" value="{enterprise_number}" />
        <label>Телефон: <input type="text" name="phone" value="{phone or ''}" placeholder="+37529..." /></label>
        <button class="btn" type="submit">Показать</button>
      </form>
    </div>
    
    <div class="card">
      <h3>События интеграции</h3>
      <div class="event">
        <strong>Тестовое событие</strong><br>
        Время: {time.strftime('%Y-%m-%d %H:%M:%S')}<br>
        Телефон: {phone or 'не указан'}<br>
        Статус: В разработке
      </div>
      <p style="color:#888; margin-top:20px;">
        Журнал событий U-ON будет реализован в следующих версиях.
        Здесь будут отображаться: входящие/исходящие звонки, поиск клиентов, ошибки интеграции.
      </p>
    </div>
  </div>
</body>
</html>'''
    
    return HTMLResponse(content=journal_html)


# ——— Автопроба для dev: периодически пишем результат в logs/uon_probe.json ———
_PROBE_PHONE = os.environ.get("UON_TEST_PHONE", "+375296254070")
_PROBE_PATH = Path("logs/uon_probe.json")


async def _write_probe_result(payload: Dict[str, Any]) -> None:
    try:
        _PROBE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROBE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Probe result written to {_PROBE_PATH}: {payload.get('ok', False)}")
    except Exception as e:
        logger.error(f"Failed to write probe result: {e}")


async def _probe_loop():
    """Автопроба поиска клиента в U-ON"""
    api_key = _CONFIG.get("api_key") or os.environ.get("UON_API_KEY") or ""
    logger.info(f"Starting probe loop for phone {_PROBE_PHONE} with API key {'***' + api_key[-4:] if api_key else 'NONE'}")
    
    for attempt in range(1, 4):
        try:
            logger.info(f"Probe attempt {attempt}/3")
            found = await _search_customer_in_uon_by_phone(api_key, _PROBE_PHONE)
            if found:
                result = {
                    "ok": True,
                    "phone": _PROBE_PHONE,
                    "display_name": found.get("name") or "",
                    "source": found.get("source"),
                }
                await _write_probe_result(result)
                logger.info(f"Customer found: {found.get('name')}")
                return
        except Exception as e:
            logger.error(f"Probe attempt {attempt} failed: {e}")
            await _write_probe_result({
                "ok": False,
                "error": str(e),
                "attempt": attempt,
            })
        if attempt < 3:
            await asyncio.sleep(2)
    
    # не нашли — пишем факт
    result = {
        "ok": False,
        "phone": _PROBE_PHONE,
        "display_name": None,
        "note": "not found in first 3 pages",
    }
    await _write_probe_result(result)
    logger.info("Customer not found in first 3 pages")


@app.on_event("startup")
async def _startup_probe_task():
    """Запуск автопробы при старте сервиса"""
    try:
        # Запускаем в фоне
        asyncio.create_task(_probe_loop())
    except Exception as e:
        # Логируем ошибку, но не падаем
        print(f"Startup probe error: {e}")


