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
_RECENT_WINDOW_SEC = 10.0  # Увеличиваем окно антидублирования

# Наш публичный URL для приёма вебхуков из U‑ON
_DEFAULT_WEBHOOK_URL = "https://bot.vochi.by/uon/webhook"


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


async def _register_client_change_webhooks(api_key: str) -> Dict[str, Any]:
    """Регистрирует вебхуки клиента: 3=Создание клиента, 4=Изменение клиента.
    POST /{key}/webhook/create.json, url = наш /uon/webhook
    Возвращает {status, created: [{type_id, id, data}], errors: [...]}.
    """
    try:
        results: Dict[str, Any] = {"status": 0, "created": [], "errors": []}
        async with await _uon_client() as client:
            for t in (3, 4):
                url = f"https://api.u-on.ru/{api_key}/webhook/create.json"
                payload = {"type_id": t, "method": "POST", "url": _DEFAULT_WEBHOOK_URL}
                try:
                    r = await client.post(url, json=payload)
                    ok = (r.status_code == 200)
                    data = None
                    try:
                        data = r.json()
                    except Exception:
                        data = {"status": r.status_code}
                    if ok:
                        results["created"].append({"type_id": t, "id": (data.get("id") if isinstance(data, dict) else None), "data": data})
                    else:
                        results["errors"].append({"type_id": t, "status": r.status_code, "data": data})
                except Exception as e:
                    results["errors"].append({"type_id": t, "error": str(e)})
        results["status"] = 200 if results["created"] else 0
        return results
    except Exception as e:
        return {"status": 0, "error": str(e)}

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


async def search_client_by_phone_enhanced(api_url: str, api_key: str, phone: str) -> dict:
    """Расширенный поиск клиента по номеру телефона в U-ON для обогащенных уведомлений"""
    try:
        # Нормализуем номер телефона
        phone_normalized = phone.strip()
        if not phone_normalized.startswith("+"):
            digits = ''.join(c for c in phone_normalized if c.isdigit())
            if digits.startswith("375") and len(digits) == 12:
                phone_normalized = f"+{digits}"
            else:
                phone_normalized = f"+{digits}"
        
        # Формируем URL для запроса
        if not api_url.startswith("https://api.u-on.ru"):
            # Если передан поддомен типа https://id67054.u-on.ru/
            api_base = "https://api.u-on.ru"
        else:
            api_base = api_url.rstrip('/')
        
        full_url = f"{api_base}/{api_key}/user.json"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(full_url)
            
            if response.status_code != 200:
                logger.error(f"U-ON API error: {response.status_code} - {response.text}")
                return {"error": f"HTTP {response.status_code}", "found": False}
            
            data = response.json()
            users = data.get("users", [])
            
            # Ищем клиента по телефону
            for user in users:
                user_phones = [
                    user.get("u_phone", "").strip(),
                    user.get("u_phone_mobile", "").strip(), 
                    user.get("u_phone_home", "").strip()
                ]
                
                # Проверяем все номера клиента
                for user_phone in user_phones:
                    if user_phone and user_phone == phone_normalized:
                        return {
                            "found": True,
                            "user_id": user.get("u_id"),
                            "global_user_id": user.get("global_u_id"),
                            "name": f"{user.get('u_surname', '')} {user.get('u_name', '')}".strip(),
                            "full_name": f"{user.get('u_surname', '')} {user.get('u_name', '')} {user.get('u_sname', '')}".strip(),
                            "phone": phone_normalized,
                            "manager_id": user.get("manager_id"),
                            "user_type": user.get("u_type", 1),  # 1 = физлицо
                            "company": user.get("u_company", "").strip(),
                            "email": user.get("u_email", "").strip(),
                            "last_update": user.get("u_date_update"),
                            "client_type": "individual" if not user.get("u_company", "").strip() else "company"
                        }
            
            # Клиент не найден
            return {"found": False, "phone": phone_normalized}
            
    except Exception as e:
        logger.error(f"Error searching client by phone {phone}: {e}")
        return {"error": str(e), "found": False}


async def create_client_in_uon(api_url: str, api_key: str, phone: str, name: str = None) -> dict:
    """Создает нового клиента в U-ON и возвращает его данные"""
    try:
        # Нормализуем номер телефона
        phone_normalized = phone.strip()
        if not phone_normalized.startswith("+"):
            digits = ''.join(c for c in phone_normalized if c.isdigit())
            if digits.startswith("375") and len(digits) == 12:
                phone_normalized = f"+{digits}"
            else:
                phone_normalized = f"+{digits}"
        
        # Формируем URL для создания клиента
        api_base = "https://api.u-on.ru"
        create_url = f"{api_base}/{api_key}/user/create.json"
        
        # Подготавливаем данные для создания
        client_data = {
            "u_phone": phone_normalized,
            "u_surname": name or "Клиент",
            "u_name": "Неизвестный" if not name else "",
            "source_id": 0  # Источник: телефонный звонок
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(create_url, json=client_data)
            
            if response.status_code == 200:
                result = response.json()
                user_id = result.get("user_id") or result.get("id")  # API может вернуть как "id", так и "user_id"
                if result.get("result") == 200 and user_id:
                    logger.info(f"✅ Created new client in U-ON: ID={user_id}, phone={phone_normalized}")
                    return {
                        "success": True,
                        "user_id": user_id,
                        "global_user_id": result.get("global_user_id"),
                        "phone": phone_normalized,
                        "name": f"{client_data['u_surname']} {client_data['u_name']}".strip(),
                        "created": True
                    }
                else:
                    logger.error(f"U-ON create client failed: {result}")
                    return {"success": False, "error": f"API error: {result.get('message', 'Unknown error')}", "created": False}
            else:
                logger.error(f"U-ON create client HTTP error: {response.status_code} - {response.text}")
                return {"success": False, "error": f"HTTP {response.status_code}", "created": False}
                
    except Exception as e:
        logger.error(f"Error creating client in U-ON for {phone}: {e}")
        return {"success": False, "error": str(e), "created": False}


def generate_enriched_notification(client_data: dict, call_info: dict, config: dict, auto_create_enabled: bool = False) -> str:
    """Генерирует обогащенное HTML-уведомление для U-ON"""
    try:
        import html
        from datetime import datetime
        
        # Убрали время - больше не используется
        
        # Определяем поддомен для ссылок
        api_url = config.get("api_url", "")
        if "id" in api_url and ".u-on.ru" in api_url:
            # Извлекаем поддомен из URL типа https://id67054.u-on.ru/
            subdomain = api_url.replace("https://", "").replace("http://", "").rstrip("/")
        else:
            subdomain = "app.u-on.ru"  # fallback
        
        phone = call_info.get("phone", "")
        line = call_info.get("line", "")
        direction = call_info.get("direction", "incoming")
        
        # Базовая информация
        direction_emoji = "📞" if direction in ["incoming", "in"] else "📱"
        direction_text = "Входящий звонок" if direction in ["incoming", "in"] else "Исходящий звонок"
        
        if client_data.get("found") or client_data.get("created"):
            # Клиент найден или только что создан
            user_id = client_data.get("user_id")
            
            if client_data.get("created"):
                # Клиент только что создан автоматически
                display_name = client_data.get("name", phone)
                status_info = "ℹ️ Клиент создан автоматически"
                html_text = f"""{direction_emoji} <b>{direction_text}</b><br/>
👤 <a href="https://{subdomain}/client_edit.php?client_id={user_id}" target="_blank">{html.escape(display_name)}</a><br/>
📱 {phone}<br/>
🏢 Линия: {line}<br/>
{status_info}"""
            else:
                # Существующий клиент
                full_name = html.escape(client_data.get("full_name", ""))
                company = client_data.get("company", "").strip()
                client_type = client_data.get("client_type", "individual")
                
                if client_type == "company" and company:
                    # Корпоративный клиент
                    html_text = f"""{direction_emoji} <b>{direction_text}</b><br/>
🏢 <a href="https://{subdomain}/client_edit.php?client_id={user_id}" target="_blank">{html.escape(company)}</a><br/>
👤 {full_name} (контакт)<br/>
📱 {phone}<br/>
🏢 Линия: {line}"""
                else:
                    # Физическое лицо
                    html_text = f"""{direction_emoji} <b>{direction_text}</b><br/>
👤 <a href="https://{subdomain}/client_edit.php?client_id={user_id}" target="_blank">{full_name}</a><br/>
📱 {phone}<br/>
🏢 Линия: {line}"""
        else:
            # Клиент не найден
            if auto_create_enabled:
                # Автосоздание включено, но что-то пошло не так
                html_text = f"""{direction_emoji} <b>{direction_text}</b><br/>
❓ Неизвестный клиент<br/>
📱 {phone}<br/>
🏢 Линия: {line}<br/>
⚠️ Не удалось создать клиента автоматически"""
            else:
                # Автосоздание выключено, показываем ссылку для ручного создания
                enterprise_number = call_info.get("enterprise_number", "")
                # Используем простую ссылку на создание с автоматическим редиректом
                create_url = f"https://bot.vochi.by/uon/admin/{enterprise_number}/create-client-and-redirect?phone={phone.replace('+', '')}"
                
                html_text = f"""{direction_emoji} <b>{direction_text}</b><br/>
❓ Неизвестный клиент<br/>
📱 {phone}<br/>
🏢 Линия: {line}<br/>
<a href="{create_url}" target="_blank">Создать клиента</a>"""
        
        return html_text
        
    except Exception as e:
        logger.error(f"Error generating enriched notification: {e}")
        # Fallback на базовое уведомление
        direction = call_info.get("direction", "incoming")
        direction_text = "Входящий звонок" if direction == "incoming" else "Исходящий звонок"
        return f"{direction_text}: {call_info.get('phone', '')}\nЛиния: {call_info.get('line', '')}\nМенеджер: {call_info.get('manager', '')}"


async def _register_default_webhook(api_key: str) -> Dict[str, Any]:
    """Регистрирует вебхук "Клик по номеру телефона клиента" в U‑ON.
    Эквивалент экрана на скришоте: Тип=Клик по номеру телефона клиента, URL=_DEFAULT_WEBHOOK_URL, Метод=POST.
    Документация: /{key}/webhook/create.json
    На практике у U‑ON отличаются имена полей в разных версиях, поэтому пробуем несколько вариантов тела.
    Возвращаем диагностический объект со статусом.
    """
    try:
        async with await _uon_client() as client:
            url = f"https://api.u-on.ru/{api_key}/webhook/create.json"
            payloads = [
                # Основной вариант: type_id 47 (Клик по номеру телефона клиента), section_id 0, method POST
                {"type_id": 47, "section_id": 0, "method": "POST", "url": _DEFAULT_WEBHOOK_URL},
                # Альтернативные названия полей (если API ожидает другие ключи)
                {"type": 47, "section": 0, "method": "POST", "url": _DEFAULT_WEBHOOK_URL},
            ]
            last_status = None
            last_body: Any = None
            for pl in payloads:
                try:
                    r = await client.post(url, json=pl)
                    last_status = r.status_code
                    try:
                        last_body = r.json()
                    except Exception:
                        last_body = {"text": r.text}
                    # Успех
                    if r.status_code == 200:
                        break
                except Exception as e:
                    last_status = -1
                    last_body = {"error": str(e)}

        # Локальная диагностика
        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_webhook_create.meta').write_text(f"HTTP_CODE={last_status}\n", encoding="utf-8")
            Path('logs/uon_webhook_create.body').write_text(json.dumps(last_body, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return {"status": last_status, "data": last_body}
    except Exception as e:
        logger.error(f"register_default_webhook error: {e}")
      
        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_webhook_create.meta').write_text("HTTP_CODE=0\n", encoding="utf-8")
            Path('logs/uon_webhook_create.body').write_text(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return {"status": 0, "error": str(e)}


async def _list_all_webhooks(api_key: str) -> Dict[str, Any]:
    """Получить список всех вебхуков аккаунта U‑ON.
    Документация: GET /{key}/webhook.json (или /webhooks.json — у U‑ON могут отличаться пути)
    Пробуем несколько вариантов.
    """
    endpoints = ["/webhook.json", "/webhooks.json", "/webhook/list.json"]
    last_status = None
    last_body: Any = None
    async with await _uon_client() as client:
        for ep in endpoints:
            url = f"https://api.u-on.ru/{api_key}{ep}"
            try:
                r = await client.get(url)
                last_status = r.status_code
                try:
                    last_body = r.json()
                except Exception:
                    last_body = {"text": r.text}
                if r.status_code == 200:
                    break
            except Exception as e:
                last_status = -1
                last_body = {"error": str(e)}
                break
    try:
        Path('logs').mkdir(exist_ok=True)
        Path('logs/uon_webhook_list.meta').write_text(f"HTTP_CODE={last_status}\n", encoding="utf-8")
        Path('logs/uon_webhook_list.body').write_text(json.dumps(last_body, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {"status": last_status, "data": last_body}


async def _delete_webhook(api_key: str, webhook_id: str) -> Dict[str, Any]:
    """Удалить вебхук по id. Пробуем несколько вариантов путей/методов.
    Основной: POST /{key}/webhook/delete.json с {"id": <id>}.
    Альт: DELETE /{key}/webhook/{id}.json
    """
    async with await _uon_client() as client:
        # Вариант 1: POST delete.json
        try:
            url1 = f"https://api.u-on.ru/{api_key}/webhook/delete.json"
            r1 = await client.post(url1, json={"id": webhook_id})
            try:
                body1 = r1.json()
            except Exception:
                body1 = {"text": r1.text}
            if r1.status_code == 200:
                return {"status": 200, "data": body1}
        except Exception:
            body1 = None
        # Вариант 2: DELETE /webhook/{id}.json
        try:
            url2 = f"https://api.u-on.ru/{api_key}/webhook/{webhook_id}.json"
            r2 = await client.delete(url2)
            try:
                body2 = r2.json()
            except Exception:
                body2 = {"text": r2.text}
            return {"status": r2.status_code, "data": body2}
        except Exception as e:
            return {"status": 0, "error": str(e)}


@app.get("/uon-admin/api/webhooks/{enterprise_number}")
async def admin_api_list_webhooks(enterprise_number: str):
    """Список всех вебхуков U‑ON для аккаунта предприятия (по api_key из БД)."""
    try:
        import asyncpg, json as _json
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        await conn.close()
        api_key = None
        if row and row.get("integrations_config"):
            cfg = row["integrations_config"]
            if isinstance(cfg, str):
                try:
                    cfg = _json.loads(cfg)
                except Exception:
                    cfg = None
            if isinstance(cfg, dict):
                api_key = ((cfg.get("uon") or {}).get("api_key") or "").strip()
        if not api_key:
            api_key = _CONFIG.get("api_key") or ""
        if not api_key:
            return {"success": False, "error": "U-ON api_key missing"}
        res = await _list_all_webhooks(api_key)
        return {"success": res.get("status") == 200, "status": res.get("status"), "data": res.get("data")}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.delete("/uon-admin/api/webhooks/{enterprise_number}")
async def admin_api_delete_all_webhooks(enterprise_number: str):
    """Удалить ВСЕ вебхуки аккаунта U‑ON (для чистой пере‑регистрации)."""
    try:
        import asyncpg, json as _json
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        await conn.close()
        api_key = None
        if row and row.get("integrations_config"):
            cfg = row["integrations_config"]
            if isinstance(cfg, str):
                try:
                    cfg = _json.loads(cfg)
                except Exception:
                    cfg = None
            if isinstance(cfg, dict):
                api_key = ((cfg.get("uon") or {}).get("api_key") or "").strip()
        if not api_key:
            api_key = _CONFIG.get("api_key") or ""
        if not api_key:
            return {"success": False, "error": "U-ON api_key missing"}

        listed = await _list_all_webhooks(api_key)
        data = listed.get("data") or {}
        # Попробуем собрать id из возможных структур
        ids: List[str] = []
        if isinstance(data, dict):
            # Варианты: {"webhooks": [{"id":..}, ...]} или {"data": [...]}
            for key in ("webhooks", "data", "items", "result", "message"):
                arr = data.get(key)
                if isinstance(arr, list):
                    for it in arr:
                        if isinstance(it, dict) and ("id" in it or "webhook_id" in it):
                            ids.append(str(it.get("id") or it.get("webhook_id")))
        elif isinstance(data, list):
            for it in data:
                if isinstance(it, dict) and ("id" in it or "webhook_id" in it):
                    ids.append(str(it.get("id") or it.get("webhook_id")))

        results = []
        for wid in ids:
            res = await _delete_webhook(api_key, wid)
            results.append({"id": wid, "status": res.get("status"), "data": res.get("data")})

        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_webhook_delete.body').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return {"success": True, "deleted": results}
    except Exception as e:
        return {"success": False, "error": str(e)}


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
        src = found.get("source") or {}
        raw = found.get("raw")
        if isinstance(src, dict):
            src = {**src, "raw": raw}
        else:
            src = {"raw": raw}
        return {
            "phone": phone,
            "profile": {
                "display_name": found.get("name") or "",
            },
            "source": src,
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


@app.get("/internal/uon/responsible-extension")
async def uon_responsible_extension(phone: str, enterprise_number: Optional[str] = None):
    """Возвращает extension ответственного менеджера для номера.
    Улучшено: поддержка enterprise_number, фолбэк получения api_key из БД и до-запрос client.json для извлечения manager_id.
    Формат ответа: {"extension": str|null, "manager_id": int|null}
    """
    try:
        # 0) Получаем api_key: из локальной конфигурации, либо из БД по enterprise_number, либо по единственному включённому U-ON юниту
        api_key = None
        try:
            api_key = _get_api_key_or_raise()
        except HTTPException:
            api_key = None

        import asyncpg
        cfg_row = None
        if not api_key or enterprise_number:
            conn0 = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
            if enterprise_number:
                cfg_row = await conn0.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
            else:
                cfg_row = await conn0.fetchrow("SELECT integrations_config FROM enterprises WHERE active = true AND integrations_config -> 'uon' ->> 'enabled' = 'true' LIMIT 1")
            await conn0.close()
            if cfg_row and cfg_row.get("integrations_config"):
                cfgv = cfg_row["integrations_config"]
                if isinstance(cfgv, str):
                    try:
                        cfgv = json.loads(cfgv)
                    except Exception:
                        cfgv = None
                if isinstance(cfgv, dict):
                    api_key = api_key or ((cfgv.get("uon") or {}).get("api_key") or "").strip()

        if not api_key:
            raise HTTPException(status_code=400, detail="U-ON api_key не сконфигурирован")

        # 1) Пытаемся найти клиента по номеру
        found = await _search_customer_in_uon_by_phone(api_key, phone)
        manager_id: Optional[int] = None
        raw = found.get("raw") if isinstance(found, dict) else None

        def _find_manager_id(obj: Any) -> Optional[int]:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lk = str(k).lower()
                    if any(t in lk for t in ("manager", "user")) and isinstance(v, (str, int)) and str(v).isdigit():
                        return int(v)
                    if isinstance(v, (dict, list)):
                        x = _find_manager_id(v)
                        if x is not None:
                            return x
            elif isinstance(obj, list):
                for it in obj:
                    x = _find_manager_id(it)
                    if x is not None:
                        return x
            return None

        if isinstance(raw, dict):
            manager_id = _find_manager_id(raw)

        # 1.1) Если manager_id не нашли, но есть id клиента — запрашиваем карточку клиента отдельно
        if manager_id is None and isinstance(raw, dict):
            cid = None
            for key in ("u_id", "id", "client_id"):
                if str(raw.get(key) or "").strip():
                    cid = str(raw.get(key)).strip()
                    break
            if cid:
                async with await _uon_client() as client:
                    url = f"https://api.u-on.ru/{api_key}/client.json?id={cid}"
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            body = r.json()
                            manager_id = _find_manager_id(body)
                    except Exception:
                        pass

        # 2) Читаем карту manager_id→extension для нужного предприятия
        conn = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
        if enterprise_number:
            row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
        else:
            row = await conn.fetchrow("SELECT integrations_config FROM enterprises WHERE active = true AND integrations_config -> 'uon' ->> 'enabled' = 'true' LIMIT 1")
        await conn.close()

        user_map = {}
        if row and row.get("integrations_config"):
            cfg = row["integrations_config"]
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = None
            if isinstance(cfg, dict):
                user_map = (cfg.get("uon") or {}).get("user_extensions") or {}

        mapped_ext = None
        if manager_id is not None and isinstance(user_map, dict):
            m = user_map.get(str(manager_id))
            if isinstance(m, str) and m.isdigit():
                mapped_ext = m

        return {"extension": mapped_ext, "manager_id": manager_id}
    except Exception as e:
        logger.error(f"uon_responsible_extension error: {e}")
        return {"extension": None}

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
        raw_text = None
        raw_body = await req.body()
        try:
            raw_text = raw_body.decode("utf-8") if raw_body else ""
        except Exception:
            raw_text = None
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
                    parsed = urllib.parse.parse_qs(raw_text or "") if raw_body else {}
                    data = {k: v[0] if isinstance(v, list) and v else v for k, v in parsed.items()}
                except Exception:
                    data = {}

        # 2.1) Собираем поля вида client[u_id] → data["client"]["u_id"]
        try:
            bracket_groups: Dict[str, Dict[str, Any]] = {}
            for k, v in list(data.items()):
                if isinstance(k, str) and "[" in k and "]" in k:
                    base = k.split("[", 1)[0]
                    inner = k[k.find("[") + 1:k.rfind("]")]
                    if base and inner:
                        bracket_groups.setdefault(base, {})[inner] = v
            for base, group in bracket_groups.items():
                # не перетирать JSON-строку, если есть
                if base not in data or not isinstance(data.get(base), (dict, str)):
                    data[base] = group
        except Exception:
            pass

        # 2) Извлечение полей с учётом русских ключей
        def pick(d: Dict[str, Any], candidates: List[str]):
            for k in candidates:
                if k in d:
                    v = d.get(k)
                    return v
            # попробуем без регистра
            low = {str(k).lower(): v for k, v in d.items()}
            for k in candidates:
                lk = str(k).lower()
                if lk in low:
                    v = low.get(lk)
                    return v
            return None

        uon_id = pick(data, ["uon_id", "account_id", "u_id", "аккаунт", "аккаунт_id"]) or ""
        subdomain = pick(data, ["uon_subdomain", "subdomain", "домен", "субдомен"]) or ""
        method = pick(data, ["method", "метод"]) or ""
        user_id = pick(data, ["user_id", "ID пользователя", "userid", "user"]) or ""
        phone = pick(data, ["phone", "телефон"]) or ""
        type_id = pick(data, ["type_id", "type", "тип"]) or ""
        client_raw = pick(data, ["client", "клиент"])  # может быть dict или json-строка
        client_obj = None
        try:
            if isinstance(client_raw, dict):
                client_obj = client_raw
            elif isinstance(client_raw, str) and client_raw.strip():
                client_obj = json.loads(client_raw)
            else:
                # собрать из client[u_*]
                grouped: Dict[str, Any] = {}
                for k, v in data.items():
                    if isinstance(k, str) and k.startswith("client[") and k.endswith("]"):
                        inner = k[len("client["):-1]
                        grouped[inner] = v
                if grouped:
                    client_obj = grouped
        except Exception:
            client_obj = None

        # 3) События клиента (type_id 3/4): синхронизация ФИО/телефонов
        tid = None
        try:
            tid = int(str(type_id)) if str(type_id).isdigit() else None
        except Exception:
            tid = None
        if tid in (3, 4):
            ent = await _find_enterprise_by_uon(uon_id, subdomain)
            if not ent:
                return {"ok": False, "error": "enterprise_not_found"}
            enterprise_number = ent["number"]

            ln = None
            fn = None
            mn = None
            if isinstance(client_obj, dict):
                ln = (client_obj.get("u_surname") or client_obj.get("u_surname_en") or "").strip() or None
                fn = (client_obj.get("u_name") or client_obj.get("u_name_en") or "").strip() or None
                mn = (client_obj.get("u_sname") or "").strip() or None
            ext_id = str(data.get("client_id") or (client_obj.get("u_id") if isinstance(client_obj, dict) else "") or "").strip() or None

            phones: List[str] = []
            if isinstance(client_obj, dict):
                for k in ("u_phone", "u_phone_mobile", "u_phone_home"):
                    v = client_obj.get(k)
                    if isinstance(v, str) and v.strip():
                        digits = ''.join(ch for ch in v if ch.isdigit())
                        if digits:
                            phones.append(("+" + digits) if not v.startswith("+") else "+" + digits)

            is_primary = False
            try:
                import asyncpg, json as _json
                connp = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
                rowp = await connp.fetchrow("SELECT integrations_config FROM enterprises WHERE number = $1", enterprise_number)
                await connp.close()
                cfg = rowp["integrations_config"] if rowp else None
                if isinstance(cfg, str):
                    try:
                        cfg = _json.loads(cfg)
                    except Exception:
                        cfg = None
                if isinstance(cfg, dict):
                    uon_cfg = cfg.get("uon") or {}
                    if isinstance(uon_cfg, dict) and bool(uon_cfg.get("primary")):
                        is_primary = True
                    smart_cfg = cfg.get("smart") or {}
                    if isinstance(smart_cfg, dict) and str(smart_cfg.get("primary") or "").lower() == "uon":
                        is_primary = True
            except Exception:
                is_primary = False

            if ext_id and phones:
                try:
                    from app.services.customers import merge_customer_identity
                    for ph in phones:
                        await merge_customer_identity(
                            enterprise_number=str(enterprise_number),
                            phone_e164=str(ph),
                            source="uon",
                            external_id=str(ext_id),
                            fio={"last_name": ln, "first_name": fn, "middle_name": mn},
                            set_primary=is_primary,
                        )
                except Exception:
                    pass
            return {"ok": True, "handled": "client", "type_id": tid, "phones": len(phones)}

        # 4) Интересует только метод call
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

        # 6) Нормализация телефона: если нет phone, берём из client[u_phone]
        if (not phone) and isinstance(client_obj, dict):
            phone = (
                str(client_obj.get("u_phone") or client_obj.get("u_phone_mobile") or "").strip()
            )
            # client может прислать с плюсом
            if phone.startswith("+"):
                phone = phone[1:]
        phone_e164 = phone
        if phone_e164 and not phone_e164.startswith("+"):
            phone_e164 = "+" + phone_e164

        # 6.1) Имя клиента для CallerID(name)
        display_name = None
        if isinstance(client_obj, dict):
            ln = str(client_obj.get("u_surname") or client_obj.get("last_name") or "").strip()
            fn = str(client_obj.get("u_name") or client_obj.get("first_name") or "").strip()
            display_name = (f"{ln} {fn}".strip() if (ln or fn) else None)

        # 7) Вызываем asterisk.py
        res = await _asterisk_make_call(code=internal_extension, phone=phone_e164, client_id=ent["secret"], display=display_name)

        # 8) Локальный лог (вход, парсинг и результат)
        try:
            Path('logs').mkdir(exist_ok=True)
            Path('logs/uon_webhook_call.meta').write_text(
                f"content_type={content_type}\n"
                f"uon_id={uon_id}\nsubdomain={subdomain}\nmanager_id={user_id}\n"
                f"ext={internal_extension}\nphone={phone_e164}\n", encoding="utf-8")
            Path('logs/uon_webhook_call.body').write_text(json.dumps({
                "raw": raw_text,
                "form": data,
                "client": client_obj,
                "display": display_name,
                "asterisk": res,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
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

async def _asterisk_make_call(code: str, phone: str, client_id: str, display: Optional[str] = None) -> Dict[str, Any]:
    try:
        async with await _uon_client() as client:
            url = "http://localhost:8018/api/makecallexternal"
            params = {"code": code, "phone": phone, "clientId": client_id}
            if display:
                params["display"] = display
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
    
    <!-- Блок дополнительных настроек -->
    <div class="card" style="margin-top:20px;">
      <h2 style="margin:0 0 15px 0; font-size:20px; color:#e7eef8;">Дополнительные настройки</h2>
      
      <div style="margin-top:20px;">
        <h3 style="margin:0 0 15px 0; font-size:18px; color:#e7eef8;">Уведомления</h3>
        
        <div style="margin-bottom:15px;">
          <div style="color:#a8c0e0; font-size:14px; margin-bottom:8px;">Уведомления о звонке</div>
          <div style="display:flex; gap:20px; align-items:center; margin-bottom:10px;">
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="callNotifyMode" value="none" style="width:16px; height:16px; accent-color:#2563eb;">
              Не уведомлять
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="callNotifyMode" value="during" checked style="width:16px; height:16px; accent-color:#2563eb;">
              Во время дозвона
            </label>
          </div>
        </div>
        
        <div style="display:flex; gap:20px; align-items:center;">
          <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="notifyIncoming" style="width:16px; height:16px; accent-color:#2563eb;" checked>
            Уведомлять при входящем
          </label>
          <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="notifyOutgoing" style="width:16px; height:16px; accent-color:#2563eb;">
            Уведомлять при исходящем
          </label>
        </div>
      </div>
      
      <div style="margin-top:30px;">
        <h3 style="margin:0 0 15px 0; font-size:18px; color:#e7eef8;">Действие при входящем звонке</h3>
        
        <div style="margin-bottom:15px;">
          <label style="display:flex; align-items:center; gap:8px; margin:0 0 10px 0; color:#e7eef8; cursor:pointer;">
            <input type="checkbox" id="createClientOnCall" style="width:16px; height:16px; accent-color:#2563eb;" checked>
            Создание заказчика при неизвестном звонке
          </label>
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="color:#a8c0e0; font-size:14px; margin-bottom:8px;">Создание обращения</div>
          <div style="display:flex; gap:15px; align-items:center; margin-bottom:10px;">
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createRequest" value="none" style="width:16px; height:16px; accent-color:#2563eb;">
              Не создавать
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createRequest" value="if_no_open" checked style="width:16px; height:16px; accent-color:#2563eb;">
              Если нет открытых обращений
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createRequest" value="if_no_request" style="width:16px; height:16px; accent-color:#2563eb;">
              Если нет обращений
            </label>
          </div>
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
            <label style="color:#a8c0e0; font-size:14px; flex:1;">Статус обращения</label>
            <button id="refreshStatusBtn" type="button" style="padding:4px 8px; background:#374151; color:#e7eef8; border:none; border-radius:4px; font-size:12px; cursor:pointer;">🔄 Обновить</button>
          </div>
          <select id="requestStatus" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
            <option value="work">В работе</option>
            <option value="new">Новое</option>
            <option value="pending">Ожидает</option>
          </select>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Источник обращения</label>
          <input type="text" id="requestSource" value="Входящий звонок" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="color:#a8c0e0; font-size:14px; margin-bottom:8px;">Создание задачи</div>
          <div style="display:flex; gap:15px; align-items:center; margin-bottom:10px;">
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createTask" value="none" checked style="width:16px; height:16px; accent-color:#2563eb;">
              Не создавать
            </label>
            <label style="display:flex; align-items:center; gap:8px; margin:0; color:#e7eef8; cursor:pointer;">
              <input type="radio" name="createTask" value="on_missed" style="width:16px; height:16px; accent-color:#2563eb;">
              Создавать при пропущенном звонке
            </label>
          </div>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Кол-во минут на выполнение задачи</label>
          <input type="number" id="taskMinutes" value="15" min="1" max="999" style="width:100px; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Изменение статуса обращения при пропущенном вызове</label>
          <select id="missedCallStatus" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
            <option value="missed">Пропущенный</option>
            <option value="pending">Ожидает</option>
            <option value="work">В работе</option>
            <option value="no_change">Не изменять</option>
          </select>
        </div>
      </div>
      
      <!-- Действие при исходящем звонке -->
      <div style="background:#1a2b42; border-radius:8px; padding:20px; margin-bottom:20px;">
        <h3 style="color:#e7eef8; margin:0 0 20px 0; font-size:18px; font-weight:600;">📞 Действие при исходящем звонке</h3>
        
        <div style="margin-bottom:15px;">
          <label style="display:flex; align-items:center; gap:8px; color:#e7eef8; font-size:14px; cursor:pointer;">
            <input type="checkbox" id="createClientOnOutgoingCall" style="margin:0;">
            Создание заказчика при неизвестном звонке
          </label>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Создание обращения</label>
          <div style="display:flex; gap:15px; flex-wrap:wrap;">
            <label style="display:flex; align-items:center; gap:8px; color:#e7eef8; font-size:14px; cursor:pointer;">
              <input type="radio" name="createOutgoingRequest" value="none" id="createOutgoingRequestNone" style="margin:0;" checked>
              Не создавать
            </label>
            <label style="display:flex; align-items:center; gap:8px; color:#e7eef8; font-size:14px; cursor:pointer;">
              <input type="radio" name="createOutgoingRequest" value="if_no_open" id="createOutgoingRequestIfNoOpen" style="margin:0;">
              Если нет открытых обращений
            </label>
            <label style="display:flex; align-items:center; gap:8px; color:#e7eef8; font-size:14px; cursor:pointer;">
              <input type="radio" name="createOutgoingRequest" value="if_no_request" id="createOutgoingRequestIfNoRequest" style="margin:0;">
              Если нет обращений
            </label>
          </div>
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
            <label style="color:#a8c0e0; font-size:14px; flex:1;">Статус обращения</label>
          </div>
          <select id="outgoingRequestStatus" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
            <option value="work">В работе</option>
            <option value="new">Новое</option>
            <option value="pending">Ожидает</option>
          </select>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Источник обращения</label>
          <input type="text" id="outgoingRequestSource" value="Исходящий звонок" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Создание задачи</label>
          <div style="display:flex; gap:15px; flex-wrap:wrap;">
            <label style="display:flex; align-items:center; gap:8px; color:#e7eef8; font-size:14px; cursor:pointer;">
              <input type="radio" name="createOutgoingTask" value="none" id="createOutgoingTaskNone" style="margin:0;" checked>
              Не создавать
            </label>
            <label style="display:flex; align-items:center; gap:8px; color:#e7eef8; font-size:14px; cursor:pointer;">
              <input type="radio" name="createOutgoingTask" value="on_missed" id="createOutgoingTaskOnMissed" style="margin:0;">
              Создавать при пропущенном звонке
            </label>
          </div>
        </div>
        
        <div style="margin-bottom:15px;">
          <label style="color:#a8c0e0; font-size:14px; margin-bottom:8px; display:block;">Кол-во минут на выполнение задачи</label>
          <input type="number" id="outgoingTaskMinutes" value="15" min="1" max="1440" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
        </div>
        
        <div style="margin-bottom:15px;">
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
            <label style="color:#a8c0e0; font-size:14px; flex:1;">Изменение статуса обращения при пропущенном вызове</label>
          </div>
          <select id="outgoingMissedCallStatus" style="width:100%; padding:8px 12px; border-radius:6px; border:1px solid #2c4a6e; background:#0b1a2a; color:#e7eef8; font-size:14px;">
            <option value="no_change">Не изменять</option>
            <option value="missed">Пропущенный</option>
            <option value="work">В работе</option>
          </select>
        </div>
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
        
        // Загружаем настройки уведомлений
        const notifications = cfg.notifications || {};
        const callModeNone = document.querySelector('input[name="callNotifyMode"][value="none"]');
        const callModeDuring = document.querySelector('input[name="callNotifyMode"][value="during"]');
        const notifyIncoming = document.getElementById('notifyIncoming');
        const notifyOutgoing = document.getElementById('notifyOutgoing');
        
        if (callModeNone && callModeDuring) {
          const mode = notifications.call_notify_mode || 'during';
          callModeNone.checked = (mode === 'none');
          callModeDuring.checked = (mode === 'during');
        }
        if (notifyIncoming) {
          notifyIncoming.checked = notifications.notify_incoming !== false;
        }
        if (notifyOutgoing) {
          notifyOutgoing.checked = !!notifications.notify_outgoing;
        }
        
        // Загружаем настройки действий при входящем звонке
        const actions = cfg.incoming_call_actions || {};
        const createClientOnCall = document.getElementById('createClientOnCall');
        const createRequestNone = document.querySelector('input[name="createRequest"][value="none"]');
        const createRequestIfNoOpen = document.querySelector('input[name="createRequest"][value="if_no_open"]');
        const createRequestIfNoRequest = document.querySelector('input[name="createRequest"][value="if_no_request"]');
        const requestStatus = document.getElementById('requestStatus');
        const requestSource = document.getElementById('requestSource');
        const createTaskNone = document.querySelector('input[name="createTask"][value="none"]');
        const createTaskOnMissed = document.querySelector('input[name="createTask"][value="on_missed"]');
        const taskMinutes = document.getElementById('taskMinutes');
        const missedCallStatus = document.getElementById('missedCallStatus');
        
        if (createClientOnCall) {
          createClientOnCall.checked = actions.create_client_on_call !== false;
        }
        
        if (createRequestNone && createRequestIfNoOpen && createRequestIfNoRequest) {
          const createRequestMode = actions.create_request || 'if_no_open';
          createRequestNone.checked = (createRequestMode === 'none');
          createRequestIfNoOpen.checked = (createRequestMode === 'if_no_open');
          createRequestIfNoRequest.checked = (createRequestMode === 'if_no_request');
        }
        
        if (requestStatus) {
          requestStatus.value = actions.request_status || 'work';
        }
        if (requestSource) {
          requestSource.value = actions.request_source || 'Входящий звонок';
        }
        
        if (createTaskNone && createTaskOnMissed) {
          const createTaskMode = actions.create_task || 'none';
          createTaskNone.checked = (createTaskMode === 'none');
          createTaskOnMissed.checked = (createTaskMode === 'on_missed');
        }
        
        if (taskMinutes) {
          taskMinutes.value = actions.task_minutes || 15;
        }
        if (missedCallStatus) {
          missedCallStatus.value = actions.missed_call_status || 'missed';
        }
        
        // Загружаем настройки исходящих звонков
        const outgoingActions = cfg.outgoing_call_actions || {};
        
        const createClientOnOutgoingCall = document.getElementById('createClientOnOutgoingCall');
        const createOutgoingRequestNone = document.getElementById('createOutgoingRequestNone');
        const createOutgoingRequestIfNoOpen = document.getElementById('createOutgoingRequestIfNoOpen');
        const createOutgoingRequestIfNoRequest = document.getElementById('createOutgoingRequestIfNoRequest');
        const outgoingRequestStatus = document.getElementById('outgoingRequestStatus');
        const outgoingRequestSource = document.getElementById('outgoingRequestSource');
        const createOutgoingTaskNone = document.getElementById('createOutgoingTaskNone');
        const createOutgoingTaskOnMissed = document.getElementById('createOutgoingTaskOnMissed');
        const outgoingTaskMinutes = document.getElementById('outgoingTaskMinutes');
        const outgoingMissedCallStatus = document.getElementById('outgoingMissedCallStatus');
        
        if (createClientOnOutgoingCall) {
          createClientOnOutgoingCall.checked = outgoingActions.create_client_on_call !== false;
        }
        if (createOutgoingRequestNone && createOutgoingRequestIfNoOpen && createOutgoingRequestIfNoRequest) {
          const reqMode = outgoingActions.create_request || 'none';
          createOutgoingRequestNone.checked = (reqMode === 'none');
          createOutgoingRequestIfNoOpen.checked = (reqMode === 'if_no_open');
          createOutgoingRequestIfNoRequest.checked = (reqMode === 'if_no_request');
        }
        if (outgoingRequestStatus) {
          outgoingRequestStatus.value = outgoingActions.request_status || 'work';
        }
        if (outgoingRequestSource) {
          outgoingRequestSource.value = outgoingActions.request_source || 'Исходящий звонок';
        }
        if (createOutgoingTaskNone && createOutgoingTaskOnMissed) {
          const taskMode = outgoingActions.create_task || 'none';
          createOutgoingTaskNone.checked = (taskMode === 'none');
          createOutgoingTaskOnMissed.checked = (taskMode === 'on_missed');
        }
        if (outgoingTaskMinutes) {
          outgoingTaskMinutes.value = outgoingActions.task_minutes || 15;
        }
        if (outgoingMissedCallStatus) {
          outgoingMissedCallStatus.value = outgoingActions.missed_call_status || 'no_change';
        }
        
        console.log('✅ Конфигурация загружена:', cfg);
        
        // Загружаем статусы обращений после загрузки основной конфигурации
        if (cfg.enabled && cfg.api_key) {
          await loadStatusLead();
        }
      } catch(e) { 
        console.warn('load() error', e); 
      }
    }

    async function save() {
      const apiUrl = (document.getElementById('domain')||{}).value?.trim?.() || 'https://api.u-on.ru';
      const apiKey = (document.getElementById('apiKey')||{}).value?.trim?.() || '';
      const enabled = !!((document.getElementById('enabled')||{}).checked);
      
      // Собираем настройки уведомлений
      const callModeNone = document.querySelector('input[name="callNotifyMode"][value="none"]');
      const callModeDuring = document.querySelector('input[name="callNotifyMode"][value="during"]');
      const notifyIncoming = document.getElementById('notifyIncoming');
      const notifyOutgoing = document.getElementById('notifyOutgoing');
      
      const notifications = {
        call_notify_mode: (callModeNone && callModeNone.checked) ? 'none' : 'during',
        notify_incoming: !!(notifyIncoming && notifyIncoming.checked),
        notify_outgoing: !!(notifyOutgoing && notifyOutgoing.checked)
      };
      
      // Собираем настройки действий при входящем звонке
      const createClientOnCall = document.getElementById('createClientOnCall');
      const createRequestNone = document.querySelector('input[name="createRequest"][value="none"]');
      const createRequestIfNoOpen = document.querySelector('input[name="createRequest"][value="if_no_open"]');
      const createRequestIfNoRequest = document.querySelector('input[name="createRequest"][value="if_no_request"]');
      const requestStatus = document.getElementById('requestStatus');
      const requestSource = document.getElementById('requestSource');
      const createTaskNone = document.querySelector('input[name="createTask"][value="none"]');
      const createTaskOnMissed = document.querySelector('input[name="createTask"][value="on_missed"]');
      const taskMinutes = document.getElementById('taskMinutes');
      const missedCallStatus = document.getElementById('missedCallStatus');
      
      let createRequestMode = 'if_no_open';
      if (createRequestNone && createRequestNone.checked) createRequestMode = 'none';
      else if (createRequestIfNoRequest && createRequestIfNoRequest.checked) createRequestMode = 'if_no_request';
      
      let createTaskMode = 'none';
      if (createTaskOnMissed && createTaskOnMissed.checked) createTaskMode = 'on_missed';
      
      const incoming_call_actions = {
        create_client_on_call: !!(createClientOnCall && createClientOnCall.checked),
        create_request: createRequestMode,
        request_status: (requestStatus && requestStatus.value) || 'work',
        request_source: (requestSource && requestSource.value) || 'Входящий звонок',
        create_task: createTaskMode,
        task_minutes: parseInt((taskMinutes && taskMinutes.value) || '15'),
        missed_call_status: (missedCallStatus && missedCallStatus.value) || 'missed'
      };
      
      // Собираем данные исходящих звонков
      const createClientOnOutgoingCall = document.getElementById('createClientOnOutgoingCall');
      const createOutgoingRequestNone = document.getElementById('createOutgoingRequestNone');
      const createOutgoingRequestIfNoOpen = document.getElementById('createOutgoingRequestIfNoOpen');
      const createOutgoingRequestIfNoRequest = document.getElementById('createOutgoingRequestIfNoRequest');
      const outgoingRequestStatus = document.getElementById('outgoingRequestStatus');
      const outgoingRequestSource = document.getElementById('outgoingRequestSource');
      const createOutgoingTaskNone = document.getElementById('createOutgoingTaskNone');
      const createOutgoingTaskOnMissed = document.getElementById('createOutgoingTaskOnMissed');
      const outgoingTaskMinutes = document.getElementById('outgoingTaskMinutes');
      const outgoingMissedCallStatus = document.getElementById('outgoingMissedCallStatus');
      
      let outgoingCreateRequestMode = 'none';
      if (createOutgoingRequestNone && createOutgoingRequestNone.checked) outgoingCreateRequestMode = 'none';
      if (createOutgoingRequestIfNoOpen && createOutgoingRequestIfNoOpen.checked) outgoingCreateRequestMode = 'if_no_open';
      if (createOutgoingRequestIfNoRequest && createOutgoingRequestIfNoRequest.checked) outgoingCreateRequestMode = 'if_no_request';
      
      let outgoingCreateTaskMode = 'none';
      if (createOutgoingTaskNone && createOutgoingTaskNone.checked) outgoingCreateTaskMode = 'none';
      if (createOutgoingTaskOnMissed && createOutgoingTaskOnMissed.checked) outgoingCreateTaskMode = 'on_missed';
      
      const outgoing_call_actions = {
        create_client_on_call: !!(createClientOnOutgoingCall && createClientOnOutgoingCall.checked),
        create_request: outgoingCreateRequestMode,
        request_status: (outgoingRequestStatus && outgoingRequestStatus.value) || 'work',
        request_source: (outgoingRequestSource && outgoingRequestSource.value) || 'Исходящий звонок',
        create_task: outgoingCreateTaskMode,
        task_minutes: parseInt((outgoingTaskMinutes && outgoingTaskMinutes.value) || '15'),
        missed_call_status: (outgoingMissedCallStatus && outgoingMissedCallStatus.value) || 'no_change'
      };
      
      const btn = document.getElementById('saveBtn');
      const msg = document.getElementById('msg');
      if (msg) { msg.textContent=''; msg.className='hint'; }
      if (btn) btn.disabled = true;
      try {
        let r = await fetch(`./api/config/${enterprise}`, { 
          method:'PUT', 
          headers:{'Content-Type':'application/json'}, 
          body: JSON.stringify({api_url: apiUrl, api_key: apiKey, enabled, notifications, incoming_call_actions, outgoing_call_actions}) 
        });
        const jr = await r.json();
        if(!jr.success) throw new Error(jr.error||'Ошибка сохранения');
        if (msg) { msg.textContent='Сохранено'; msg.className='hint success'; }
        // Пытаемся зарегистрировать вебхук после сохранения (сервер сделает это сам, но покажем статус)
        try {
          if (jr.webhook) {
            msg.textContent += ` • Вебхук: HTTP ${jr.webhook.status||0}`;
          }
        } catch(_){ }
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

    async function loadStatusLead() {
      try {
        const r = await fetch(`./api/status-lead/${enterprise}`);
        const j = await r.json();
        
        if (j.success && j.statuses) {
          populateStatusSelects(j.statuses);
          console.log('✅ Статусы обращений загружены:', j.statuses);
        } else {
          console.warn('Не удалось загрузить статусы обращений:', j.error || 'Unknown error');
          // Используем статусы по умолчанию
          populateStatusSelects([
            {id: 'work', name: 'В работе'},
            {id: 'new', name: 'Новое'},
            {id: 'pending', name: 'Ожидает'},
            {id: 'missed', name: 'Пропущенный'},
            {id: 'no_change', name: 'Не изменять'}
          ]);
        }
      } catch(e) {
        console.warn('loadStatusLead() error', e);
        // Используем статусы по умолчанию
        populateStatusSelects([
          {id: 'work', name: 'В работе'},
          {id: 'new', name: 'Новое'},
          {id: 'pending', name: 'Ожидает'},
          {id: 'missed', name: 'Пропущенный'},
          {id: 'no_change', name: 'Не изменять'}
        ]);
      }
    }

    function populateStatusSelects(statuses) {
      const requestStatus = document.getElementById('requestStatus');
      const missedCallStatus = document.getElementById('missedCallStatus');
      const outgoingRequestStatus = document.getElementById('outgoingRequestStatus');
      const outgoingMissedCallStatus = document.getElementById('outgoingMissedCallStatus');
      
      if (requestStatus) {
        // Сохраняем текущее значение
        const currentValue = requestStatus.value;
        requestStatus.innerHTML = '';
        
        statuses.forEach(status => {
          const option = document.createElement('option');
          option.value = status.id;
          option.textContent = status.name;
          requestStatus.appendChild(option);
        });
        
        // Восстанавливаем значение или ставим "В работе" по умолчанию
        requestStatus.value = currentValue || 'work';
      }
      
      if (missedCallStatus) {
        // Сохраняем текущее значение
        const currentValue = missedCallStatus.value;
        missedCallStatus.innerHTML = '';
        
        // Добавляем специальную опцию "Не изменять"
        const noChangeOption = document.createElement('option');
        noChangeOption.value = 'no_change';
        noChangeOption.textContent = 'Не изменять';
        missedCallStatus.appendChild(noChangeOption);
        
        statuses.forEach(status => {
          if (status.id !== 'no_change') { // Избегаем дублирования
            const option = document.createElement('option');
            option.value = status.id;
            option.textContent = status.name;
            missedCallStatus.appendChild(option);
          }
        });
        
        // Восстанавливаем значение или ставим "Пропущенный" по умолчанию
        missedCallStatus.value = currentValue || 'missed';
      }
      
      // Заполняем исходящие списки
      if (outgoingRequestStatus) {
        const currentValue = outgoingRequestStatus.value;
        outgoingRequestStatus.innerHTML = '';
        
        statuses.forEach(status => {
          const option = document.createElement('option');
          option.value = status.id;
          option.textContent = status.name;
          outgoingRequestStatus.appendChild(option);
        });
        
        outgoingRequestStatus.value = currentValue || 'work';
      }
      
      if (outgoingMissedCallStatus) {
        const currentValue = outgoingMissedCallStatus.value;
        outgoingMissedCallStatus.innerHTML = '';
        
        // Добавляем специальную опцию "Не изменять"
        const noChangeOption = document.createElement('option');
        noChangeOption.value = 'no_change';
        noChangeOption.textContent = 'Не изменять';
        outgoingMissedCallStatus.appendChild(noChangeOption);
        
        statuses.forEach(status => {
          if (status.id !== 'no_change') {
            const option = document.createElement('option');
            option.value = status.id;
            option.textContent = status.name;
            outgoingMissedCallStatus.appendChild(option);
          }
        });
        
        outgoingMissedCallStatus.value = currentValue || 'no_change';
      }
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
    const refreshStatusBtn = document.getElementById('refreshStatusBtn');
    
    if (saveBtn) saveBtn.addEventListener('click', save);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteIntegration);
    if (refreshBtn) refreshBtn.addEventListener('click', refresh);
    if (journalBtn) journalBtn.addEventListener('click', openJournal);
    if (refreshStatusBtn) refreshStatusBtn.addEventListener('click', async () => {
      refreshStatusBtn.disabled = true;
      refreshStatusBtn.textContent = '🔄 Загрузка...';
      try {
        await loadStatusLead();
        refreshStatusBtn.textContent = '✅ Обновлено';
        setTimeout(() => {
          refreshStatusBtn.textContent = '🔄 Обновить';
          refreshStatusBtn.disabled = false;
        }, 2000);
      } catch(e) {
        refreshStatusBtn.textContent = '❌ Ошибка';
        setTimeout(() => {
          refreshStatusBtn.textContent = '🔄 Обновить';
          refreshStatusBtn.disabled = false;
        }, 2000);
      }
    });

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
        notifications = uon_config.get("notifications", {})
        incoming_call_actions = uon_config.get("incoming_call_actions", {})
        outgoing_call_actions = uon_config.get("outgoing_call_actions", {})
        return {
            "api_url": uon_config.get("api_url", "https://api.u-on.ru"),
            "api_key": uon_config.get("api_key", ""),
            "enabled": uon_config.get("enabled", False),
            "log_calls": uon_config.get("log_calls", False),
            "notifications": {
                "call_notify_mode": notifications.get("call_notify_mode", "during"),  # "none" или "during"
                "notify_incoming": notifications.get("notify_incoming", True),
                "notify_outgoing": notifications.get("notify_outgoing", False)
            },
            "incoming_call_actions": {
                "create_client_on_call": incoming_call_actions.get("create_client_on_call", True),
                "create_request": incoming_call_actions.get("create_request", "if_no_open"),  # "none", "if_no_open", "if_no_request"
                "request_status": incoming_call_actions.get("request_status", "work"),
                "request_source": incoming_call_actions.get("request_source", "Входящий звонок"),
                "create_task": incoming_call_actions.get("create_task", "none"),  # "none", "on_missed"
                "task_minutes": incoming_call_actions.get("task_minutes", 15),
                "missed_call_status": incoming_call_actions.get("missed_call_status", "missed")
            },
            "outgoing_call_actions": {
                "create_client_on_call": outgoing_call_actions.get("create_client_on_call", False),
                "create_request": outgoing_call_actions.get("create_request", "none"),  # "none", "if_no_open", "if_no_request"
                "request_status": outgoing_call_actions.get("request_status", "work"),
                "request_source": outgoing_call_actions.get("request_source", "Исходящий звонок"),
                "create_task": outgoing_call_actions.get("create_task", "none"),  # "none", "on_missed"
                "task_minutes": outgoing_call_actions.get("task_minutes", 15),
                "missed_call_status": outgoing_call_actions.get("missed_call_status", "no_change")
            }
        }
    except Exception as e:
        logger.error(f"Error getting config for {enterprise_number}: {e}")
        return {"error": str(e)}


@app.put("/uon-admin/api/config/{enterprise_number}")
async def admin_api_put_config(enterprise_number: str, config: dict):
    """Сохранить конфигурацию U-ON для предприятия"""
    try:
        import asyncpg, json
        
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Читаем текущую конфигурацию, чтобы не затирать user_extensions
        existing_cfg_row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        existing_uon: dict = {}
        if existing_cfg_row and existing_cfg_row.get("integrations_config") is not None:
            raw_cfg = existing_cfg_row["integrations_config"]
            if isinstance(raw_cfg, str):
                try:
                    raw_cfg = json.loads(raw_cfg) or {}
                except Exception:
                    raw_cfg = {}
            if isinstance(raw_cfg, dict):
                existing_uon = (raw_cfg.get("uon") or {}) if isinstance(raw_cfg.get("uon"), dict) else {}

        # Берём имеющуюся карту назначений, если в запросе не передали новую
        existing_user_ext = {}
        if isinstance(existing_uon, dict):
            existing_user_ext = existing_uon.get("user_extensions") or {}

        incoming_user_ext = config.get("user_extensions")
        if not isinstance(incoming_user_ext, dict):
            incoming_user_ext = None

        # Обрабатываем настройки уведомлений
        existing_notifications = existing_uon.get("notifications", {})
        incoming_notifications = config.get("notifications")
        if isinstance(incoming_notifications, dict):
            # Если пришли новые настройки уведомлений, обновляем их
            notifications_config = {
                "call_notify_mode": incoming_notifications.get("call_notify_mode", existing_notifications.get("call_notify_mode", "during")),
                "notify_incoming": incoming_notifications.get("notify_incoming", existing_notifications.get("notify_incoming", True)),
                "notify_outgoing": incoming_notifications.get("notify_outgoing", existing_notifications.get("notify_outgoing", False))
            }
        else:
            # Оставляем существующие настройки
            notifications_config = existing_notifications
            
        # Обрабатываем настройки действий при входящем звонке
        existing_actions = existing_uon.get("incoming_call_actions", {})
        incoming_actions = config.get("incoming_call_actions")
        if isinstance(incoming_actions, dict):
            # Если пришли новые настройки действий, обновляем их
            actions_config = {
                "create_client_on_call": incoming_actions.get("create_client_on_call", existing_actions.get("create_client_on_call", True)),
                "create_request": incoming_actions.get("create_request", existing_actions.get("create_request", "if_no_open")),
                "request_status": incoming_actions.get("request_status", existing_actions.get("request_status", "work")),
                "request_source": incoming_actions.get("request_source", existing_actions.get("request_source", "Входящий звонок")),
                "create_task": incoming_actions.get("create_task", existing_actions.get("create_task", "none")),
                "task_minutes": incoming_actions.get("task_minutes", existing_actions.get("task_minutes", 15)),
                "missed_call_status": incoming_actions.get("missed_call_status", existing_actions.get("missed_call_status", "missed"))
            }
        else:
            # Оставляем существующие настройки
            actions_config = existing_actions
            
        # Обрабатываем настройки действий при исходящем звонке
        existing_outgoing_actions = existing_uon.get("outgoing_call_actions", {})
        incoming_outgoing_actions = config.get("outgoing_call_actions")
        if isinstance(incoming_outgoing_actions, dict):
            # Если пришли новые настройки действий, обновляем их
            outgoing_actions_config = {
                "create_client_on_call": incoming_outgoing_actions.get("create_client_on_call", existing_outgoing_actions.get("create_client_on_call", False)),
                "create_request": incoming_outgoing_actions.get("create_request", existing_outgoing_actions.get("create_request", "none")),
                "request_status": incoming_outgoing_actions.get("request_status", existing_outgoing_actions.get("request_status", "work")),
                "request_source": incoming_outgoing_actions.get("request_source", existing_outgoing_actions.get("request_source", "Исходящий звонок")),
                "create_task": incoming_outgoing_actions.get("create_task", existing_outgoing_actions.get("create_task", "none")),
                "task_minutes": incoming_outgoing_actions.get("task_minutes", existing_outgoing_actions.get("task_minutes", 15)),
                "missed_call_status": incoming_outgoing_actions.get("missed_call_status", existing_outgoing_actions.get("missed_call_status", "no_change"))
            }
        else:
            # Оставляем существующие настройки
            outgoing_actions_config = existing_outgoing_actions

        # Формируем новую конфигурацию, НЕ трогая user_extensions без явного запроса
        uon_config = {
            "api_url": config.get("api_url", existing_uon.get("api_url", "https://api.u-on.ru")),
            "api_key": config.get("api_key", existing_uon.get("api_key", "")),
            "enabled": config.get("enabled", existing_uon.get("enabled", False)),
            "log_calls": config.get("log_calls", existing_uon.get("log_calls", False)),
            "notifications": notifications_config,
            "incoming_call_actions": actions_config,
            "outgoing_call_actions": outgoing_actions_config,
            "user_extensions": incoming_user_ext if incoming_user_ext is not None else existing_user_ext,
            "webhooks": existing_uon.get("webhooks", {}),
        }
        
        # Обновляем в БД используя jsonb_set
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

        # Удаляем старые вебхуки при пересохранении/выключении
        try:
            old_hooks = existing_uon.get("webhooks", {}) if isinstance(existing_uon, dict) else {}
            old_ids = []
            for group in ("client", "click_phone"):
                ids = old_hooks.get(group) or []
                if isinstance(ids, list):
                    old_ids.extend([i for i in ids if i])
            if old_ids and existing_uon.get("api_key"):
                try:
                    del_results = []
                    for wid in old_ids:
                        try:
                            res = await _delete_webhook(existing_uon.get("api_key"), wid)
                            del_results.append({"id": wid, "status": res.get("status"), "data": res.get("data")})
                        except Exception as ee:
                            del_results.append({"id": wid, "error": str(ee)})
                    # Очистим поле webhooks в БД
                    import asyncpg
                    conn_d = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
                    await conn_d.execute(
                        """
                        UPDATE enterprises SET integrations_config = jsonb_set(
                            integrations_config,
                            '{uon,webhooks}',
                            '{}'::jsonb,
                            true
                        ) WHERE number = $1
                        """,
                        enterprise_number,
                    )
                    await conn_d.close()
                except Exception as _:
                    pass
        except Exception:
            pass

        # Если интеграция включена — регистрируем новые вебхуки в U‑ON
        reg_status: Dict[str, Any] = {"status": 0}
        reg_clients: Dict[str, Any] = {"status": 0}
        try:
            if uon_config.get("enabled") and uon_config.get("api_key"):
                reg_status = await _register_default_webhook(uon_config.get("api_key"))
                reg_clients = await _register_client_change_webhooks(uon_config.get("api_key"))
                # Сохраняем ID вебхуков в БД (подключимся заново, чтобы не держать прошлое соединение)
                try:
                    import asyncpg
                    conn2 = await asyncpg.connect(host="localhost", port=5432, database="postgres", user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg==")
                    webhooks_cfg = existing_uon.get("webhooks", {}) if isinstance(existing_uon, dict) else {}
                    client_ids = [item.get("id") for item in (reg_clients.get("created") or []) if isinstance(item, dict)]
                    click_ids = [item.get("id") for item in (reg_status.get("created") or []) if isinstance(item, dict)]
                    webhooks_cfg.update({
                        "client": client_ids,
                        "click_phone": click_ids,
                    })
                    await conn2.execute(
                        """
                        UPDATE enterprises SET integrations_config = jsonb_set(
                            integrations_config,
                            '{uon,webhooks}',
                            $2::jsonb,
                            true
                        ) WHERE number = $1
                        """,
                        enterprise_number,
                        json.dumps(webhooks_cfg),
                    )
                    await conn2.close()
                except Exception as ee:
                    logger.error(f"save webhooks ids failed: {ee}")
        except Exception as e:
            logger.error(f"Webhook register error: {e}")
        
        return {"success": True, "message": "Configuration saved", "webhook": reg_status, "webhooks_client": reg_clients}
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


async def _should_send_notification(enterprise_number: str, direction: str, phase: str) -> bool:
    """Проверяет, нужно ли отправлять уведомление на основе настроек.
    
    Args:
        enterprise_number: номер предприятия
        direction: "incoming" или "outgoing"
        phase: "dial" (во время дозвона) или "hangup" (после завершения)
    
    Returns:
        True если нужно отправлять уведомление
    """
    try:
        import asyncpg, json
        
        conn = await asyncpg.connect(
            host="localhost", port=5432, database="postgres", 
            user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        await conn.close()
        
        if not row or not row.get("integrations_config"):
            return False
            
        cfg = row["integrations_config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
            
        uon_config = cfg.get("uon", {}) if isinstance(cfg, dict) else {}
        notifications = uon_config.get("notifications", {})
        
        # Проверяем режим уведомлений о звонке
        call_notify_mode = notifications.get("call_notify_mode", "during")
        if call_notify_mode == "none":
            return False
        if call_notify_mode == "during" and phase != "dial":
            return False
            
        # Проверяем настройки по направлению
        if direction in ["incoming", "in"]:
            return notifications.get("notify_incoming", True)
        elif direction in ["outgoing", "out"]:
            return notifications.get("notify_outgoing", False)
            
        return False
        
    except Exception as e:
        logger.error(f"Error checking notification settings for {enterprise_number}: {e}")
        return False

@app.post("/internal/uon/notify-incoming")
async def internal_notify_incoming(payload: dict, request: Request):
    """Внутренний вызов: отправить всплывашку при реальном звонке.
    Ожидает: { enterprise_number, phone, extension, direction?, phase? }
    Текст: "Фамилия Имя клиента — Фамилия Имя менеджера (ext)".
    """
    try:
        enterprise_number = str(payload.get("enterprise_number") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        extension = str(payload.get("extension") or "").strip()
        
        extensions_all = payload.get("extensions_all") or []
        direction = str(payload.get("direction") or "incoming").strip()  # "incoming" или "outgoing"  
        phase = str(payload.get("phase") or "dial").strip()  # "dial" или "hangup"
        
        # integration_cache теперь корректно передает direction (in/out)
        
        # Логируем источник вызова для отладки дублирования
        import uuid
        call_uuid = str(uuid.uuid4())[:8]
        client_host = request.client.host if request.client else "unknown"
        logger.info(f"🔔 notify-incoming [{call_uuid}] called from {client_host} for {phone} ext:{extension}")
        logger.info(f"📋 [{call_uuid}] Payload: enterprise={enterprise_number}, direction={direction}, phase={phase}")
        
        # УСИЛЕННОЕ антидублирование на входе в функцию
        import time
        digits = _normalize_phone_digits(phone)
        global_key = (enterprise_number, digits, extension)
        now = time.time()
        last_call = _RECENT_NOTIFIES.get(f"ENTRY_{global_key}")
        if last_call and (now - last_call) < 3.0:  # 3 секунды между вызовами
            logger.info(f"🚫 [{call_uuid}] Duplicate call blocked for {phone} ext:{extension} (last call {now - last_call:.1f}s ago)")
            return {"success": True, "status": 200, "blocked": "duplicate_entry"}
        _RECENT_NOTIFIES[f"ENTRY_{global_key}"] = now
        
        # Проверяем настройки уведомлений
        should_notify = await _should_send_notification(enterprise_number, direction, phase)
        if not should_notify:
            logger.info(f"Notification skipped for {enterprise_number} {direction} {phase} due to settings")
            return {"success": True, "skipped": True, "reason": "disabled_by_settings"}
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

        # Получаем конфигурацию для обогащенных уведомлений
        api_url = None
        enriched_notifications_enabled = True  # По умолчанию включено
        try:
            uon_config = cfg.get("uon", {}) if isinstance(cfg, dict) else {}
            api_url = uon_config.get("api_url", "https://api.u-on.ru")
            # В будущем добавим настройку enriched_notifications_enabled
        except Exception:
            api_url = "https://api.u-on.ru"

        # Поиск клиента для обогащенного уведомления
        client_data = None
        auto_create_enabled = False  # TODO: Получать из настроек интеграции
        
        if enriched_notifications_enabled:
            try:
                # Сначала ищем существующего клиента
                client_data = await search_client_by_phone_enhanced(api_url, api_key, phone)
                logger.info(f"🔍 Client search for {phone}: found={client_data.get('found', False)}")
                
                # Если клиент не найден и включено автосоздание - создаем
                if not client_data.get("found") and auto_create_enabled:
                    logger.info(f"🆕 Auto-creating client for unknown phone: {phone}")
                    create_result = await create_client_in_uon(api_url, api_key, phone)
                    
                    if create_result.get("success"):
                        # Клиент успешно создан
                        client_data = create_result
                        logger.info(f"✅ Auto-created client: ID={create_result.get('user_id')}")
                    else:
                        logger.error(f"❌ Failed to auto-create client: {create_result.get('error')}")
                        
            except Exception as e:
                logger.error(f"Error in enhanced client search: {e}")
                client_data = None
        
        # Fallback: формируем имя клиента старым способом если обогащенный поиск не сработал
        customer_name = None
        if not client_data or not client_data.get("found"):
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

        # Находим ALL uon user_id для ВСЕХ extensions из события
        matched_managers = []  # [(manager_id, extension, ext_norm), ...]
        ext_raw = str(extension)
        ext_norm = ''.join(ch for ch in ext_raw if ch.isdigit())
        
        # Создаем список всех extensions для проверки (основной + extensions_all)
        all_exts_to_check = [extension] + (extensions_all or [])
        
        if isinstance(user_extensions, dict):
            for check_ext in all_exts_to_check:
                check_ext_str = str(check_ext).strip()
                check_ext_norm = ''.join(ch for ch in check_ext_str if ch.isdigit())
                
                for uid, mapped_ext in user_extensions.items():
                    try:
                        mapped_ext_str = str(mapped_ext).strip()
                    except Exception:
                        mapped_ext_str = str(mapped_ext)
                    
                    # Проверяем совпадение (нормализованное и точное)
                    if mapped_ext_str == check_ext_norm or mapped_ext_str == check_ext_str:
                        # Проверяем, что этот manager_id еще не добавлен
                        if not any(m[0] == uid for m in matched_managers):
                            matched_managers.append((uid, check_ext_str, check_ext_norm))
        
        # Fallback: если никого не нашли, используем старую логику для основного extension
        manager_id = None
        if matched_managers:
            manager_id = matched_managers[0][0]  # Первый найденный для совместимости
        else:
            # Старая логика для обратной совместимости
            if isinstance(user_extensions, dict):
                for uid, ext in user_extensions.items():
                    try:
                        ext_str = str(ext).strip()
                    except Exception:
                        ext_str = str(ext)
                    if ext_str == ext_norm or ext_str == extension:
                        manager_id = uid
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

        # Формируем текст уведомления
        if enriched_notifications_enabled:
            # Используем обогащенное уведомление (даже для неизвестных клиентов)
            call_info = {
                "phone": phone,
                "line": f"{enterprise_number}-june",
                "manager": manager_name or extension or "менеджер",
                "direction": direction,
                "enterprise_number": enterprise_number
            }
            config_for_enrichment = {
                "api_url": api_url
            }
            # Если client_data None, создаем базовую структуру для неизвестного клиента
            if client_data is None:
                client_data = {"found": False, "phone": phone}
            text = generate_enriched_notification(client_data, call_info, config_for_enrichment, auto_create_enabled)
            logger.info(f"📱 [{call_uuid}] Generated enriched notification for {phone}")
        else:
            # Fallback на старый формат
            text = f"{customer_name} — {manager_name or 'менеджер'}"
            if extension:
                text += f" ({extension})"
            logger.info(f"📞 Using basic notification format for {phone}")

        # ЛОГИКА ДЛЯ ИСХОДЯЩИХ ЗВОНКОВ: только инициатору
        if direction in ["outgoing", "out"]:
            # Для исходящих звонков отправляем только тому менеджеру, который инициировал звонок
            if manager_id:
                broadcast_ids = []  # Не используем broadcast для исходящих
                logger.info(f"Outgoing call: sending notification only to initiator manager_id={manager_id}")
            else:
                logger.info(f"Outgoing call: no manager_id found, skipping notification")
                return {"success": False, "error": "outgoing_call_no_manager"}
        # ЛОГИКА ДЛЯ ВХОДЯЩИХ ЗВОНКОВ: всем найденным менеджерам
        elif len(matched_managers) > 1:
            # Отправляем всем найденным менеджерам
            broadcast_ids = [m[0] for m in matched_managers]
            logger.info(f"Incoming call: sending notifications to multiple managers: {broadcast_ids} for extensions: {[m[1] for m in matched_managers]}")
        elif not manager_id:
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
        else:
            broadcast_ids = []
            
        # Если есть broadcast_ids (множественная отправка), отправляем всем
        if broadcast_ids:
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


@app.get("/uon-admin/api/status-lead/{enterprise_number}")
async def admin_api_get_status_lead(enterprise_number: str):
    """Получить список статусов обращений из U-ON для выпадающих списков"""
    try:
        import asyncpg, json, httpx
        
        # Подключаемся к БД и получаем API ключ
        conn = await asyncpg.connect(
            host="localhost", port=5432, database="postgres", 
            user="postgres", password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        row = await conn.fetchrow(
            "SELECT integrations_config FROM enterprises WHERE number = $1",
            enterprise_number
        )
        await conn.close()
        
        if not row or not row.get("integrations_config"):
            return {"success": False, "error": "Enterprise config not found"}
            
        cfg = row["integrations_config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
            
        uon_config = cfg.get("uon", {}) if isinstance(cfg, dict) else {}
        api_key = uon_config.get("api_key", "").strip()
        
        if not api_key:
            return {"success": False, "error": "U-ON API key not configured"}
        
        # Запрашиваем статусы обращений из U-ON API
        async with await _uon_client() as client:
            url = f"https://api.u-on.ru/{api_key}/status_lead.json"
            r = await client.get(url)
            
            if r.status_code != 200:
                return {"success": False, "error": f"U-ON API error: {r.status_code}", "status_code": r.status_code}
            
            try:
                data = r.json()
            except Exception as e:
                return {"success": False, "error": f"Invalid JSON response: {e}"}
            
            # Обрабатываем ответ и извлекаем статусы
            statuses = []
            if isinstance(data, dict):
                # Проверяем разные возможные структуры ответа
                status_list = data.get("records") or data.get("statuses") or data.get("status_lead") or data.get("data") or []
                if isinstance(status_list, list):
                    for status_item in status_list:
                        if isinstance(status_item, dict):
                            status_id = status_item.get("id") or status_item.get("status_id")
                            status_name = status_item.get("name") or status_item.get("title") or status_item.get("status_name")
                            is_archive = status_item.get("is_archive", 0)
                            
                            # Включаем все статусы (и активные, и архивные)
                            if status_id and status_name:
                                # Заменяем стрелку на дефис для корректного отображения
                                display_name = status_name.replace("&rarr;", "-").replace("→", "-")
                                statuses.append({
                                    "id": str(status_id), 
                                    "name": display_name,
                                    "is_archive": bool(is_archive)
                                })
                        elif isinstance(status_item, str):
                            # Если это просто строки
                            statuses.append({"id": status_item, "name": status_item})
                            
            return {
                "success": True, 
                "statuses": statuses,
                "total": len(statuses),
                "raw_response": data  # Для отладки
            }
            
    except Exception as e:
        logger.error(f"Error getting status_lead for {enterprise_number}: {e}")
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


@app.post("/admin/{enterprise_number}/api/test-enriched-notification")
async def admin_api_test_enriched_notification(enterprise_number: str, payload: dict):
    """Тест обогащенных уведомлений"""
    try:
        # Получаем конфигурацию U-ON
        cfg = await admin_api_get_config(enterprise_number)
        if not cfg.get("enabled") or not cfg.get("api_key"):
            return {"success": False, "error": "U-ON integration not configured"}
        
        phone = payload.get("phone", "+375296254070")
        manager_id = payload.get("manager_id", "4")
        
        # Ищем клиента
        logger.info(f"🔍 Searching client by phone: {phone}")
        client_data = await search_client_by_phone_enhanced(cfg["api_url"], cfg["api_key"], phone)
        
        # Формируем информацию о звонке
        call_info = {
            "phone": phone,
            "line": f"{enterprise_number}-june",
            "manager": "152",
            "direction": "incoming"
        }
        
        # Генерируем обогащенное уведомление
        call_info["enterprise_number"] = enterprise_number  # Добавляем enterprise_number
        enriched_text = generate_enriched_notification(client_data, call_info, cfg)
        
        # Отправляем уведомление
        logger.info(f"📤 Sending enriched notification to manager {manager_id}")
        
        api_base = "https://api.u-on.ru"
        notification_url = f"{api_base}/{cfg['api_key']}/notification/create.json"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(notification_url, json={
                "text": enriched_text,
                "manager_id": manager_id
            })
            
            notification_result = response.json() if response.status_code == 200 else {"error": f"HTTP {response.status_code}"}
        
        return {
            "success": True,
            "client_search": client_data,
            "enriched_text": enriched_text,
            "notification_result": notification_result
        }
        
    except Exception as e:
        logger.error(f"Error testing enriched notification: {e}")
        return {"success": False, "error": str(e)}


@app.post("/uon/admin/{enterprise_number}/api/create-client")
async def admin_api_create_client_on_demand(enterprise_number: str, payload: dict):
    """Создает клиента в U-ON по требованию и возвращает URL для перехода"""
    try:
        # Получаем конфигурацию U-ON
        cfg = await admin_api_get_config(enterprise_number)
        if not cfg.get("enabled") or not cfg.get("api_key"):
            return {"success": False, "error": "U-ON integration not configured"}
        
        phone = payload.get("phone", "")
        name = payload.get("name", "")
        
        if not phone:
            return {"success": False, "error": "Phone number required"}
        
        # Создаем клиента
        logger.info(f"🆕 Creating client on demand: {phone}")
        result = await create_client_in_uon(cfg["api_url"], cfg["api_key"], phone, name)
        
        if result.get("success"):
            # Формируем URL для перехода
            api_url = cfg.get("api_url", "")
            if "id" in api_url and ".u-on.ru" in api_url:
                subdomain = api_url.replace("https://", "").replace("http://", "").rstrip("/")
            else:
                subdomain = "app.u-on.ru"
            
            client_url = f"https://{subdomain}/client_edit.php?client_id={result['user_id']}"
            
            return {
                "success": True,
                "client_id": result["user_id"],
                "redirect_url": client_url,
                "message": f"Клиент создан успешно: ID={result['user_id']}"
            }
        else:
            return {
                "success": False,
                "error": result.get("error", "Failed to create client")
            }
            
    except Exception as e:
        logger.error(f"Error creating client on demand: {e}")
        return {"success": False, "error": str(e)}


@app.get("/uon/admin/{enterprise_number}/create-client-and-redirect")
async def admin_create_client_and_redirect(enterprise_number: str, phone: str = None):
    """Создает клиента в U-ON и перенаправляет на его карточку"""
    try:
        from fastapi.responses import RedirectResponse, HTMLResponse
        
        if not phone:
            return HTMLResponse("<h1>Ошибка: Не указан номер телефона</h1>", status_code=400)
        
        # Нормализуем номер телефона
        phone_normalized = phone.strip()
        if not phone_normalized.startswith("+"):
            digits = ''.join(c for c in phone_normalized if c.isdigit())
            if digits.startswith("375") and len(digits) == 12:
                phone_normalized = f"+{digits}"
            else:
                phone_normalized = f"+{digits}"
        
        # Получаем конфигурацию U-ON
        cfg = await admin_api_get_config(enterprise_number)
        if not cfg.get("enabled") or not cfg.get("api_key"):
            return HTMLResponse("<h1>Ошибка: U-ON интеграция не настроена</h1>", status_code=400)
        
        # Создаем клиента
        result = await create_client_in_uon(cfg["api_url"], cfg["api_key"], phone_normalized, "Новый клиент")
        
        if result.get("success"):
            # Формируем URL для перехода
            api_url = cfg.get("api_url", "")
            if "id" in api_url and ".u-on.ru" in api_url:
                subdomain = api_url.replace("https://", "").replace("http://", "").rstrip("/")
            else:
                subdomain = "app.u-on.ru"
            
            client_url = f"https://{subdomain}/client_edit.php?client_id={result['user_id']}"
            
            # Перенаправляем на карточку клиента
            return RedirectResponse(url=client_url, status_code=302)
        else:
            error_message = result.get("error", "Неизвестная ошибка")
            return HTMLResponse(f"<h1>Ошибка создания клиента</h1><p>{error_message}</p>", status_code=500)
            
    except Exception as e:
        logger.error(f"Error in create-client-and-redirect: {e}")
        return HTMLResponse(f"<h1>Внутренняя ошибка</h1><p>{str(e)}</p>", status_code=500)


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


