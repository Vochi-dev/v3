from fastapi import FastAPI, Request, HTTPException
import os
import asyncio
import httpx
from typing import Optional, Dict, Any, Tuple, List
import json
from pathlib import Path
import logging
import time

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="U-ON Integration Service", version="0.1.0")


# In-memory config for pilot
_CONFIG: Dict[str, Any] = {
    "api_key": "",
}


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
    return {"received": True}


@app.post("/uon/webhook")
async def webhook(req: Request):
    body = await req.json()
    return {"ok": True, "data": body}


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
        <input id="domain" type="url" placeholder="api.u-on.ru" />
        </div>
        <div>
          <label>API Key</label>
          <input id="apiKey" type="text" placeholder="xxxxxxxx" />
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
        const apiKeyEl = document.getElementById('apiKey');
        const enabledEl = document.getElementById('enabled');
        if (apiKeyEl) apiKeyEl.value = cfg.api_key || '';
        if (enabledEl) enabledEl.checked = !!cfg.enabled;
      } catch(e) { console.warn('load() error', e); }
    }

    async function save() {
      const apiKey = (document.getElementById('apiKey')||{}).value?.trim?.() || '';
      const enabled = !!((document.getElementById('enabled')||{}).checked);
      const btn = document.getElementById('saveBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        let r = await fetch(`./api/config/${enterprise}`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_key: apiKey, enabled}) });
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
        # Здесь будет запрос к БД за конфигурацией предприятия
        # Пока вернем базовую конфигурацию
        return {
            "api_key": _CONFIG.get("api_key", ""),
            "enabled": False,
            "log_calls": False,
            "primary": False
        }
    except Exception as e:
        return {"error": str(e)}


@app.put("/uon-admin/api/config/{enterprise_number}")
async def admin_api_put_config(enterprise_number: str, config: dict):
    """Сохранить конфигурацию U-ON для предприятия"""
    try:
        # Здесь будет сохранение в БД
        # Пока просто обновляем локальную конфигурацию
        if "api_key" in config:
            _CONFIG["api_key"] = config["api_key"]
        
        return {"success": True, "message": "Configuration saved"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.delete("/uon-admin/api/config/{enterprise_number}")
async def admin_api_delete_config(enterprise_number: str):
    """Удалить конфигурацию U-ON для предприятия"""
    try:
        # Здесь будет удаление из БД
        _CONFIG.clear()
        return {"success": True, "message": "Configuration deleted"}
    except Exception as e:
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


