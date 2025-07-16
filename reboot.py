#!/usr/bin/env python3
# reboot.py — сервис мониторинга состояния юнитов и записи в БД
import asyncio
import asyncpg
import logging
import os
import sys
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn
import json
import subprocess
import requests
import time

# === КОНСТАНТЫ ===
SSH_USER = 'root'
SSH_PASS = '5atx9Ate@pbx'
SSH_PORT = 5059
SSH_TIMEOUT = 10
DB_DSN = 'postgresql://postgres:r%2FYskqh%2FZbZuvjb2b3ahfg%3D%3D@localhost:5432/postgres'
LOG_FILE = 'reboot_service.log'
POLL_INTERVAL = 60  # секунд

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('reboot')

# === ПАРСЕР SIP SHOW PEERS ===
def parse_sip_peers(output: str) -> dict:
    lines = output.strip().split('\n')
    gsm_total = gsm_online = sip_total = sip_online = internal_total = internal_online = 0
    for line in lines:
        if 'Name/username' in line or 'sip peers' in line or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        name_part = parts[0]
        peer_name = name_part.split('/')[0]
        is_online = " OK " in line
        if peer_name.startswith('000') and len(peer_name) == 7:
            gsm_total += 1
            if is_online:
                gsm_online += 1
        elif len(peer_name) == 3 and peer_name.isdigit():
            if peer_name not in ['301', '302']:
                internal_total += 1
                if is_online:
                    internal_online += 1
        elif peer_name not in ['301', '302']:
            sip_total += 1
            if is_online:
                sip_online += 1
    return {
        'gsm_total': gsm_total,
        'gsm_online': gsm_online,
        'sip_total': sip_total,
        'sip_online': sip_online,
        'internal_total': internal_total,
        'internal_online': internal_online
    }

# === SSH КОМАНДЫ ===
async def run_ssh_command(ip: str, command: str) -> tuple[str, str, int]:
    ssh_cmd = [
        'sshpass', '-p', SSH_PASS,
        'ssh',
        '-o', 'ConnectTimeout=5',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'LogLevel=ERROR',
        '-p', str(SSH_PORT),
        f'{SSH_USER}@{ip}',
        f'timeout {SSH_TIMEOUT} {command}'
    ]
    proc = await asyncio.create_subprocess_exec(
        *ssh_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SSH_TIMEOUT+2)
        return stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore'), proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        return '', 'Connection timeout', 1

# === ОПРОС ОДНОГО ХОСТА ===
async def check_single_host(ip: str, enterprise_number: str) -> dict:
    result = {
        'enterprise_number': enterprise_number,
        'ip': ip,
        'status': 'offline',
        'disk_usage_percent': None,
        'line_stats': None,
        'sip_peers': None,
        'error_message': None
    }
    # SIP
    sip_out, sip_err, sip_code = await run_ssh_command(ip, 'asterisk -rx "sip show peers"')
    # DF
    df_out, df_err, df_code = await run_ssh_command(ip, "df -h / | tail -1 | awk '{print $5}' | sed 's/%//'")
    # Анализ
    if sip_code == 0:
        result['line_stats'] = parse_sip_peers(sip_out)
        result['sip_peers'] = sum(result['line_stats'].values())
        result['status'] = 'online'
    else:
        result['error_message'] = sip_err.strip() or 'SIP command failed'
    if df_code == 0:
        try:
            result['disk_usage_percent'] = int(df_out.strip())
        except Exception:
            result['disk_usage_percent'] = None
    else:
        result['error_message'] = (result['error_message'] or '') + f' | DF: {df_err.strip()}'
    return result

# === ЗАПИСЬ В unit_status_history ===
async def log_unit_status_history(enterprise_number, prev_status, new_status, failure_counter, action_type, action_result, user_initiator, extra_info=None):
    import asyncpg
    print(f"[DEBUG] DB_DSN (log_unit_status_history): {DB_DSN}")
    print(f"[DEBUG] repr(DB_DSN): {repr(DB_DSN)}")
    # Используем глобальную DB_DSN
    conn = await asyncpg.connect(DB_DSN)
    try:
        await conn.execute(
            """
            INSERT INTO unit_status_history (enterprise_number, prev_status, new_status, change_time, failure_counter, action_type, action_result, user_initiator, extra_info)
            VALUES ($1, $2, $3, now(), $4, $5, $6, $7, $8)
            """,
            enterprise_number, prev_status, new_status, failure_counter, action_type, action_result, user_initiator, json.dumps(extra_info) if extra_info else None
        )
    finally:
        await conn.close()

# === ФУНКЦИЯ ПЕРЕЗАГРУЗКИ EWELINK ===
def reboot_ewelink_device(device_id, api_url="http://localhost:8010", enterprise_number=None, prev_status=None, failure_counter=None, user_initiator="auto"):
    action_result = ""
    extra_info = {}
    try:
        # --- ВЫКЛЮЧЕНИЕ ---
        logger.info(f"[DEBUG] Отправляю запрос на выключение устройства {device_id} через {api_url}/toggle")
        print(f"[DEBUG] POST {api_url}/toggle: device_id={device_id}, state=False")
        resp_off = requests.post(f"{api_url}/toggle", json={"device_id": device_id, "state": False}, timeout=10)
        extra_info["off_response"] = resp_off.text
        if enterprise_number:
            asyncio.create_task(log_unit_status_history(enterprise_number, prev_status, "off", failure_counter, "ewelink_toggle_off", "success" if resp_off.ok and resp_off.json().get("success") else "fail", user_initiator, {"device_id": device_id, "state": False}))
        # --- ПАУЗА ---
        logger.info("[DEBUG] Пауза 5 секунд перед включением устройства")
        time.sleep(5)
        # --- ВКЛЮЧЕНИЕ ---
        logger.info(f"[DEBUG] Отправляю запрос на включение устройства {device_id} через {api_url}/toggle")
        print(f"[DEBUG] POST {api_url}/toggle: device_id={device_id}, state=True")
        resp_on = requests.post(f"{api_url}/toggle", json={"device_id": device_id, "state": True}, timeout=10)
        extra_info["on_response"] = resp_on.text
        if enterprise_number:
            asyncio.create_task(log_unit_status_history(enterprise_number, "off", "on", failure_counter, "ewelink_toggle_on", "success" if resp_on.ok and resp_on.json().get("success") else "fail", user_initiator, {"device_id": device_id, "state": True}))
        return resp_off.ok and resp_on.ok
    except Exception as e:
        logger.error(f"Ошибка в reboot_ewelink_device: {e}")
        return False

# === ФУНКЦИИ ДЛЯ РАБОТЫ С GOIP ===
async def get_goip_device_with_flag(enterprise_number):
    """Получить GoIP устройство с custom_boolean_flag = true для предприятия"""
    import asyncpg
    conn = await asyncpg.connect(DB_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT gateway_name FROM goip WHERE enterprise_number = $1 AND custom_boolean_flag = true",
            enterprise_number
        )
        return row['gateway_name'] if row else None
    finally:
        await conn.close()

async def reboot_goip_device(gateway_name, enterprise_number=None, prev_status=None, failure_counter=None, user_initiator="auto"):
    """Перезагрузка GoIP устройства через HTTP API"""
    goip_api_url = "http://localhost:8008"
    extra_info = {"gateway_name": gateway_name}
    
    try:
        logger.info(f"[GOIP] Отправляю запрос на перезагрузку GoIP устройства {gateway_name}")
        response = requests.post(f"{goip_api_url}/devices/{gateway_name}/reboot", timeout=30)
        
        extra_info["response_status"] = response.status_code
        extra_info["response_text"] = response.text
        
        if response.status_code == 200:
            logger.info(f"[GOIP] GoIP устройство {gateway_name} успешно перезагружено")
            action_result = "success"
        else:
            logger.error(f"[GOIP] Ошибка при перезагрузке GoIP устройства {gateway_name}: HTTP {response.status_code}")
            action_result = "fail"
        
        # Логируем операцию в unit_status_history
        if enterprise_number:
            await log_unit_status_history(
                enterprise_number, 
                prev_status, 
                "goip_reboot_initiated", 
                failure_counter, 
                "goip_reboot", 
                action_result, 
                user_initiator, 
                extra_info
            )
        
        return response.status_code == 200
        
    except Exception as e:
        logger.error(f"[GOIP] Критическая ошибка при перезагрузке GoIP устройства {gateway_name}: {e}")
        if enterprise_number:
            await log_unit_status_history(
                enterprise_number, 
                prev_status, 
                "goip_reboot_error", 
                failure_counter, 
                "goip_reboot", 
                "error", 
                user_initiator, 
                {"gateway_name": gateway_name, "error": str(e)}
            )
        return False

# === ОПРОС ВСЕХ ХОСТОВ ===
async def poll_all_hosts(pool):
    async with pool.acquire() as conn:
        enterprises = await conn.fetch("SELECT number, ip, parameter_option_2, host, LENGTH(host) as host_length FROM enterprises WHERE active AND is_enabled AND ip IS NOT NULL AND ip <> ''")
    tasks = []
    for ent in enterprises:
        tasks.append(check_single_host(ent['ip'], ent['number']))
    results = await asyncio.gather(*tasks)
    # Сохраняем в БД и реализуем логику перезагрузки ewelink
    async with pool.acquire() as conn:
        for i, res in enumerate(results):
            ent = enterprises[i]
            # Получаем предыдущие значения
            row = await conn.fetchrow("SELECT last_status, failure_counter, ewelink_action_done FROM unit_live_status WHERE enterprise_number=$1", res['enterprise_number'])
            prev_status = row['last_status'] if row else None
            failure_counter = row['failure_counter'] if row else 0
            ewelink_action_done = row['ewelink_action_done'] if row and 'ewelink_action_done' in row else False
            # Сериализация line_stats для jsonb
            if res['line_stats'] is not None:
                res['line_stats'] = json.dumps(res['line_stats'], ensure_ascii=False)
            else:
                res['line_stats'] = None
            # Логика подсчета неудачных попыток
            if res['status'] == 'offline':
                failure_counter += 1
            else:
                failure_counter = 0
                ewelink_action_done = False  # сбрасываем если снова online
            # Автоматическая перезагрузка (GoIP или eWeLink)
            if (
                res['status'] == 'offline' and
                failure_counter == 3 and
                ent['parameter_option_2'] and
                not ewelink_action_done and
                ent['host']
            ):
                host_length = ent['host_length']
                
                # Определяем тип перезагрузки по длине host
                if host_length > 10:
                    # GoIP перезагрузка для длинных host (> 10 символов)
                    try:
                        logger.info(f"{res['enterprise_number']} {res['ip']} — 3 оффлайна подряд, host='{ent['host']}' ({host_length} символов > 10), проверяем GoIP")
                        
                        # Проверяем наличие GoIP с custom_boolean_flag = true
                        goip_gateway_name = await get_goip_device_with_flag(res['enterprise_number'])
                        if goip_gateway_name:
                            logger.info(f"{res['enterprise_number']} — найден GoIP {goip_gateway_name} с custom_boolean_flag=true, перезагружаем GoIP")
                            success = await reboot_goip_device(goip_gateway_name, enterprise_number=res['enterprise_number'], prev_status=prev_status, failure_counter=failure_counter, user_initiator="auto")
                            if success:
                                ewelink_action_done = True
                        else:
                            logger.info(f"{res['enterprise_number']} — GoIP с custom_boolean_flag=true не найден, перезагрузка не выполняется")
                    except Exception as e:
                        logger.error(f"Ошибка перезагрузки GoIP для {res['enterprise_number']}: {e}")
                else:
                    # eWeLink перезагрузка для коротких host (<= 10 символов)
                    device_name = ent['host']
                    try:
                        logger.info(f"{res['enterprise_number']} {res['ip']} — 3 оффлайна подряд, host='{ent['host']}' ({host_length} символов <= 10), перезагружаем ewelink {device_name}")
                        reboot_ewelink_device(device_name, enterprise_number=res['enterprise_number'], prev_status=prev_status, failure_counter=failure_counter, user_initiator="auto")
                        ewelink_action_done = True
                    except Exception as e:
                        logger.error(f"Ошибка перезагрузки ewelink для {res['enterprise_number']} ({device_name}): {e}")
            # Обновляем unit_live_status
            await conn.execute(
                """
                INSERT INTO unit_live_status (enterprise_number, last_status, last_checked_at, failure_counter, last_error_message, disk_usage_percent, line_stats, sip_peers, ewelink_action_done)
                VALUES ($1, $2, now(), $3, $4, $5, $6, $7, $8)
                ON CONFLICT (enterprise_number) DO UPDATE SET
                  last_status=EXCLUDED.last_status,
                  last_checked_at=EXCLUDED.last_checked_at,
                  failure_counter=EXCLUDED.failure_counter,
                  last_error_message=EXCLUDED.last_error_message,
                  disk_usage_percent=EXCLUDED.disk_usage_percent,
                  line_stats=EXCLUDED.line_stats,
                  sip_peers=EXCLUDED.sip_peers,
                  ewelink_action_done=EXCLUDED.ewelink_action_done
                """,
                res['enterprise_number'],
                res['status'],
                failure_counter,
                res['error_message'],
                res['disk_usage_percent'],
                res['line_stats'],
                res['sip_peers'],
                ewelink_action_done
            )
            logger.info(f"{res['enterprise_number']} {res['ip']} {res['status']} disk={res['disk_usage_percent']}% lines={res['line_stats']} err={res['error_message']} fail={failure_counter} ewelink_action={ewelink_action_done}")

# === ФОНЫЙ ТАСК ===
async def poller(pool):
    while True:
        try:
            await poll_all_hosts(pool)
        except Exception as e:
            logger.error(f"Ошибка опроса всех хостов: {e}")
        await asyncio.sleep(POLL_INTERVAL)

# === FASTAPI ===
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/logs")
def get_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding='utf-8') as f:
            return JSONResponse(content={"log": f.read()})
    return JSONResponse(content={"log": "No log file."})

# === MAIN ===
if __name__ == "__main__":
    async def main():
        print(f"[DEBUG] DB_DSN (main): {DB_DSN}")
        print(f"[DEBUG] repr(DB_DSN): {repr(DB_DSN)}")
        pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=5)
        asyncio.create_task(poller(pool))
        config = uvicorn.Config(app, host="0.0.0.0", port=8009, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    asyncio.run(main()) 