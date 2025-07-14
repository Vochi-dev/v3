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

# === ОПРОС ВСЕХ ХОСТОВ ===
async def poll_all_hosts(pool):
    async with pool.acquire() as conn:
        enterprises = await conn.fetch("SELECT number, ip, parameter_option_2, host FROM enterprises WHERE active AND is_enabled AND ip IS NOT NULL AND ip <> ''")
    tasks = []
    for ent in enterprises:
        tasks.append(check_single_host(ent['ip'], ent['number']))
    results = await asyncio.gather(*tasks)
    # Сохраняем в БД
    async with pool.acquire() as conn:
        for res in results:
            # Сериализация line_stats для jsonb
            if res['line_stats'] is not None:
                res['line_stats'] = json.dumps(res['line_stats'], ensure_ascii=False)
            else:
                res['line_stats'] = None
            await conn.execute(
                """
                INSERT INTO unit_live_status (enterprise_number, last_status, last_checked_at, failure_counter, last_error_message, disk_usage_percent, line_stats, sip_peers)
                VALUES ($1, $2, now(), $3, $4, $5, $6, $7)
                ON CONFLICT (enterprise_number) DO UPDATE SET
                  last_status=EXCLUDED.last_status,
                  last_checked_at=EXCLUDED.last_checked_at,
                  failure_counter=EXCLUDED.failure_counter,
                  last_error_message=EXCLUDED.last_error_message,
                  disk_usage_percent=EXCLUDED.disk_usage_percent,
                  line_stats=EXCLUDED.line_stats,
                  sip_peers=EXCLUDED.sip_peers
                """,
                res['enterprise_number'],
                res['status'],
                0,  # failure_counter пока не считаем
                res['error_message'],
                res['disk_usage_percent'],
                res['line_stats'],
                res['sip_peers']
            )
            logger.info(f"{res['enterprise_number']} {res['ip']} {res['status']} disk={res['disk_usage_percent']}% lines={res['line_stats']} err={res['error_message']}")

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
        pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=5)
        asyncio.create_task(poller(pool))
        config = uvicorn.Config(app, host="0.0.0.0", port=8009, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    asyncio.run(main()) 