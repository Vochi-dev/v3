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
    redirect_url = "https://bot.vochi.by/ewelink-callback/"
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
    code = request.query_params.get("code")
    region = request.query_params.get("region", "eu")
    if not code:
        logging.error("/ewelink-callback/ вызван без code!")
        return {"error": "No code provided"}
    logging.info(f"Получен OAuth2 code: {code}, region: {region}")
    # Обмениваем code на токен
    ok = device_client.exchange_oauth_code(code, region=region)
    if ok:
        logging.info("Токен успешно получен и сохранён через callback!")
        return {"message": "Токен успешно получен и сохранён! Теперь сервис ewelink_api готов к работе."}
    else:
        logging.error("Ошибка обмена code на токен через callback!")
        return {"error": "Ошибка обмена code на токен! Проверьте логи."}

if __name__ == "__main__":
    port = int(os.environ.get("EWELINK_API_PORT", 8010))
    uvicorn.run("ewelink_api:app", host="0.0.0.0", port=port, log_level="info") 