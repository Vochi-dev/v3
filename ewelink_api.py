from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn
import logging
import sys
import os
import json
import time
from urllib.parse import urlencode
import requests
import asyncpg

# Импортируем класс EWeLinkDevices из ewelink_devices.py
from ewelink_devices import EWeLinkDevices

# Настройка логирования
logging.basicConfig(
    filename='ewelink_service.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

app = FastAPI(title="eWeLink API Service", description="API для управления eWeLink-устройствами", version="1.0")

device_client = EWeLinkDevices()
if not device_client.load_tokens():
    logging.error("Не удалось загрузить токены eWeLink! Авторизация требуется через CLI или callback.")
    print("❌ Не удалось загрузить токены eWeLink! Авторизация требуется через CLI или callback.")

# --- Удаляю загрузку DEVICEID_TO_ENTERPRISE ---

# Исправляю строку подключения к БД (убираю возможные переносы и пробелы)
DB_DSN = "postgresql://postgres:r/Yskqh/ZbZuvjb2b3ahfg==@localhost:5432/postgres".strip()

async def log_unit_status_history(device_id, prev_status, new_status, failure_counter, action_type, action_result, user_initiator, extra_info=None):
    try:
        conn = await asyncpg.connect(DB_DSN)
        row = await conn.fetchrow("SELECT number FROM enterprises WHERE host = $1", device_id)
        enterprise_number = row['number'] if row else None
        if not enterprise_number:
            logging.error(f"Не найден enterprise_number для device_id={device_id}, запись не будет сохранена!")
            await conn.close()
            return
        # Логируем все параметры перед вставкой
        logging.info(f"unit_status_history params: enterprise_number={enterprise_number} (type={type(enterprise_number)}), prev_status={prev_status} (type={type(prev_status)}), new_status={new_status} (type={type(new_status)}), failure_counter={failure_counter} (type={type(failure_counter)}), action_type={action_type} (type={type(action_type)}), action_result={action_result} (type={type(action_result)}), user_initiator={user_initiator} (type={type(user_initiator)}), extra_info={extra_info} (type={type(extra_info)})")
        print(f"[DEBUG] failure_counter до int: {repr(failure_counter)} (type={type(failure_counter)})")
        logging.info(f"[DEBUG] failure_counter до int: {repr(failure_counter)} (type={type(failure_counter)})")
        try:
            failure_counter = int(str(failure_counter).strip())
        except Exception as e:
            logging.error(f"Ошибка преобразования failure_counter к int: {failure_counter} ({e})")
            failure_counter = 0
        try:
            extra_info_json = json.dumps(extra_info, ensure_ascii=False) if extra_info else None
        except Exception as e:
            logging.error(f"Ошибка сериализации extra_info: {extra_info} ({e})")
            extra_info_json = None
        await conn.execute(
            """
            INSERT INTO unit_status_history (enterprise_number, prev_status, new_status, change_time, failure_counter, action_type, action_result, user_initiator, extra_info)
            VALUES ($1, $2, $3, now(), $4, $5, $6, $7, $8)
            """,
            enterprise_number, prev_status, new_status, failure_counter, action_type, action_result, user_initiator, extra_info_json
        )
        await conn.close()
        logging.info(f"unit_status_history: записано событие для {enterprise_number}")
    except Exception as e:
        logging.error(f"Ошибка записи в unit_status_history: {e} | DSN={DB_DSN}")

class ToggleRequest(BaseModel):
    device_id: str
    state: bool

@app.post("/toggle")
async def toggle_device(req: ToggleRequest):
    logging.info(f"Попытка переключить устройство {req.device_id} в состояние {'on' if req.state else 'off'}")
    if not device_client.access_token:
        logging.error("Нет access_token для управления устройствами!")
        raise HTTPException(status_code=500, detail="Нет access_token для управления устройствами!")
    result = device_client.toggle_device(req.device_id, req.state)
    # --- ЛОГИРУЕМ В unit_status_history ---
    prev_status = 'on' if req.state else 'off'
    new_status = 'off' if req.state == False else 'on'
    failure_counter = 0
    action_type = 'ewelink_toggle'
    action_result = 'success' if result else 'fail'
    user_initiator = 'api'
    extra_info = {'device_id': req.device_id, 'state': req.state}
    await log_unit_status_history(req.device_id, prev_status, new_status, failure_counter, action_type, action_result, user_initiator, extra_info)
    if result:
        logging.info(f"Устройство {req.device_id} успешно переключено в состояние {'on' if req.state else 'off'}")
        return {"success": True, "message": f"Устройство {req.device_id} переключено"}
    else:
        logging.error(f"Ошибка при переключении устройства {req.device_id}")
        raise HTTPException(status_code=500, detail="Ошибка при переключении устройства")

@app.get("/status/{device_id}")
def get_status(device_id: str):
    logging.info(f"Запрос статуса устройства {device_id}")
    if not device_client.access_token:
        logging.error("Нет access_token для получения статуса!")
        raise HTTPException(status_code=500, detail="Нет access_token для получения статуса!")
    status = device_client.get_device_status(device_id)
    if status:
        return {"device_id": device_id, "status": status.get('params', {}).get('switch', 'unknown'), "online": status.get('online', False)}
    else:
        logging.error(f"Устройство {device_id} не найдено или ошибка API")
        raise HTTPException(status_code=404, detail="Устройство не найдено или ошибка API")

@app.get("/devices")
def get_devices():
    logging.info("Запрос списка устройств")
    if not device_client.access_token:
        logging.error("Нет access_token для получения списка устройств!")
        raise HTTPException(status_code=500, detail="Нет access_token для получения списка устройств!")
    devices = device_client.get_devices(save_to_file=False)
    if devices is not None:
        return {"devices": devices}
    else:
        logging.error("Ошибка получения списка устройств")
        raise HTTPException(status_code=500, detail="Ошибка получения списка устройств")

@app.get("/ewelink-auth-url")
def get_ewelink_auth_url():
    # Генерируем OAuth2 URL для авторизации
    app_id = device_client.app_id
    app_secret = device_client.app_secret
    region = device_client.region
    nonce = str(int(time.time()))
    state = str(int(time.time()))
    seq = str(int(time.time() * 1000))
    message = f"{app_id}_{seq}"
    import hmac, hashlib, base64
    signature = base64.b64encode(hmac.new(app_secret.encode(), message.encode(), digestmod=hashlib.sha256).digest()).decode()
    redirect_url = "https://bot.vochi.by/ewelink-debug/"
    params = {
        "clientId": app_id,
        "seq": seq,
        "authorization": signature,
        "redirectUrl": redirect_url,
        "grantType": "authorization_code",
        "state": state,
        "nonce": nonce,
        "showQRCode": "false"
    }
    url = f"https://c2ccdn.coolkit.cc/oauth/index.html?{urlencode(params)}"
    logging.info(f"Сгенерирована OAuth2 ссылка для авторизации: {url}")
    return {"auth_url": url}

@app.get("/ewelink-callback/")
async def ewelink_callback(request: Request):
    """Production endpoint для получения OAuth кода от eWeLink"""
    try:
        # Получаем код и регион из query параметров
        code = request.query_params.get('code')
        region = request.query_params.get('region', 'eu')
        state = request.query_params.get('state')
        
        logging.info(f"✅ Получен OAuth код: {code[:10] if code else 'отсутствует'}... регион: {region}, state: {state}")
        
        if not code:
            logging.error("❌ OAuth код не найден в запросе")
            return {"error": "OAuth код не найден"}
            
        # Автоматически обмениваем код на токены
        success = device_client.exchange_oauth_code(code, region=region)
        
        if success:
            logging.info("🎉 Токены успешно получены и сохранены!")
            return {
                "success": True,
                "message": "Авторизация успешна! Токены обновлены.",
                "timestamp": time.time()
            }
        else:
            logging.error("❌ Не удалось обменять код на токены")
            return {
                "success": False,
                "message": "Ошибка обмена кода на токены",
                "timestamp": time.time()
            }
            
    except Exception as e:
        logging.error(f"❌ Ошибка в callback: {e}")
        return {
            "success": False,
            "message": f"Ошибка: {str(e)}",
            "timestamp": time.time()
        }

@app.get("/ewelink-debug/")
async def ewelink_debug(request: Request):
    """Диагностический эндпоинт для отладки eWeLink callback"""
    # Логируем все что приходит
    headers = dict(request.headers)
    query_params = dict(request.query_params)
    client_ip = request.client.host
    
    debug_info = {
        "method": request.method,
        "url": str(request.url),
        "client_ip": client_ip,
        "headers": headers,
        "query_params": query_params,
        "timestamp": time.time()
    }
    
    logging.info(f"[DEBUG] eWeLink request: {debug_info}")
    return {"status": "ok", "debug_info": debug_info}

@app.post("/ewelink-debug/")
async def ewelink_debug_post(request: Request):
    """Диагностический эндпоинт для POST запросов"""
    headers = dict(request.headers)
    query_params = dict(request.query_params)
    client_ip = request.client.host
    
    try:
        body = await request.body()
        body_text = body.decode('utf-8') if body else ""
    except:
        body_text = ""
    
    debug_info = {
        "method": request.method,
        "url": str(request.url),
        "client_ip": client_ip,
        "headers": headers,
        "query_params": query_params,
        "body": body_text,
        "timestamp": time.time()
    }
    
    logging.info(f"[DEBUG] eWeLink POST request: {debug_info}")
    return {"status": "ok", "debug_info": debug_info}

@app.post("/ewelink-refresh-token/")
async def refresh_token():
    """Принудительное обновление access_token через refresh_token"""
    try:
        success = device_client.refresh_access_token()
        if success:
            return {
                "success": True,
                "message": "Токен успешно обновлен!",
                "timestamp": time.time()
            }
        else:
            return {
                "success": False,
                "message": "Не удалось обновить токен. Проверьте refresh_token.",
                "timestamp": time.time()
            }
    except Exception as e:
        logging.error(f"Ошибка обновления токена: {e}")
        return {
            "success": False,
            "message": f"Ошибка: {str(e)}",
            "timestamp": time.time()
        }

@app.post("/ewelink-manual-oauth/")
async def manual_oauth_exchange(code: str, region: str = "eu"):
    """Ручная обработка OAuth кода от httpbin.org"""
    try:
        # Используем метод обмена кода на токен
        success = device_client.exchange_oauth_code(code, region=region)
        if success:
            return {
                "success": True,
                "message": "Токен успешно получен из OAuth кода!",
                "timestamp": time.time()
            }
        else:
            return {
                "success": False,
                "message": "Не удалось обменять код на токен. Проверьте код и регион.",
                "timestamp": time.time()
            }
    except Exception as e:
        logging.error(f"Ошибка обмена OAuth кода: {e}")
        return {
            "success": False,
            "message": f"Ошибка: {str(e)}",
            "timestamp": time.time()
        }

if __name__ == "__main__":
    port = int(os.environ.get("EWELINK_API_PORT", 8010))
    uvicorn.run("ewelink_api:app", host="0.0.0.0", port=port, log_level="info") 