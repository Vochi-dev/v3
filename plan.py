from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import json
from pathlib import Path
import hashlib
import logging
import re
from collections import defaultdict
import asyncio
import subprocess

app = FastAPI()

# --- Logging Setup ---
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "plan.log", mode='w'),
        logging.StreamHandler()
    ],
    force=True
)

# --- НАЧАЛО: Добавление CORS Middleware ---
origins = [
    "https://bot.vochi.by",
    "http://localhost",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- КОНЕЦ: Добавление CORS Middleware ---

DB_CONFIG = 'postgresql://postgres:r%2FYskqh%2FZbZuvjb2b3ahfg%3D%3D@127.0.0.1:5432/postgres'

class GenerateConfigRequest(BaseModel):
    enterprise_id: str

# --- Helper Functions ---
def get_node_by_id(nodes, node_id):
    """Находит узел в списке по его ID."""
    return next((n for n in nodes if n['id'] == node_id), None)

def get_target_node_id(edges, source_node_id, source_handle=None):
    """Находит ID целевого узла для данного исходного узла."""
    for edge in edges:
        if edge['source'] == source_node_id:
            if source_handle is None or edge.get('sourceHandle') == source_handle:
                return edge.get('target')
    return None
    
def get_all_target_node_ids(edges, source_node_id):
    """Находит все ID целевых узлов для данного исходного узла."""
    return [edge.get('target') for edge in edges if edge['source'] == source_node_id]

def find_first_meaningful_node(start_node_id, nodes, edges):
    """Находит ID первого 'значимого' узла, пропуская пустые транзитные узлы."""
    current_node_id = start_node_id
    visited = set()

    while current_node_id and current_node_id not in visited:
        visited.add(current_node_id)
        node = get_node_by_id(nodes, current_node_id)
        if not node:
            return None

        # Условие, при котором узел считается "пустым" и транзитным.
        # В данном случае - это узел "greeting" без указанного файла.
        is_empty_greeting = (
            node.get('type') == 'greeting' and
            not node.get('data', {}).get('greetingFile', {}).get('name')
        )
        is_start_node = node.get('type') == 'custom'
        is_passthrough_ivr = node.get('type') == 'ivr' and node.get('data', {}).get('isSingleOutput', False)

        if is_empty_greeting or is_start_node or is_passthrough_ivr:
            # Узел пустой, ищем следующий.
            current_node_id = get_target_node_id(edges, current_node_id)
        else:
            # Найден значимый узел.
            return current_node_id
    
    return None # Обнаружен цикл или конец пути.

def generate_context_name(schema_id, node_id, suffix=None):
    """Генерирует уникальное имя контекста."""
    base_string = f"{schema_id}-{node_id}"
    if suffix:
        base_string += f"-{suffix}"
    return hashlib.md5(base_string.encode()).hexdigest()[:8]

def generate_department_context_name(enterprise_id, department_number):
     return hashlib.md5(f"dep-{enterprise_id}-{department_number}".encode()).hexdigest()[:8]

async def deploy_config_to_asterisk(host_ip: str, local_config_path: str, enterprise_id: str) -> dict:
    """Деплой extensions.conf на удаленный Asterisk хост"""
    
    # SSH параметры (константы)
    SSH_PORT = 5059
    SSH_USER = "root" 
    SSH_PASSWORD = "5atx9Ate@pbx"
    REMOTE_PATH = "/etc/asterisk/extensions.conf"
    
    logging.info(f"Starting deployment to Asterisk host {host_ip} for enterprise {enterprise_id}")
    
    try:
        # 1. Копируем файл на хост
        scp_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "scp", "-P", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            local_config_path, f"{SSH_USER}@{host_ip}:{REMOTE_PATH}"
        ]
        
        logging.info(f"Copying config file to {host_ip}...")
        scp_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *scp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=10.0
        )
        
        scp_stdout, scp_stderr = await scp_result.communicate()
        
        if scp_result.returncode != 0:
            error_msg = scp_stderr.decode().strip() if scp_stderr else "Unknown SCP error"
            logging.error(f"SCP failed for {host_ip}: {error_msg}")
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
        logging.info(f"Config file successfully copied to {host_ip}")
        
        # 2. Перезагружаем диалплан и SIP
        reload_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "ssh", "-p", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            f"{SSH_USER}@{host_ip}",
            'asterisk -rx "dialplan reload" && asterisk -rx "sip reload"'
        ]
        
        logging.info(f"Reloading Asterisk on {host_ip}...")
        reload_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *reload_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=10.0
        )
        
        reload_stdout, reload_stderr = await reload_result.communicate()
        
        if reload_result.returncode != 0:
            error_msg = reload_stderr.decode().strip() if reload_stderr else "Unknown reload error"
            logging.error(f"Asterisk reload failed for {host_ip}: {error_msg}")
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
        logging.info(f"Asterisk successfully reloaded on {host_ip}")
        logging.info(f"Reload output: {reload_stdout.decode().strip()}")
        
        return {"success": True, "message": "Схемы звонков обновлены"}
        
    except asyncio.TimeoutError:
        logging.error(f"Timeout while deploying config to {host_ip}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error deploying to {host_ip}: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def deploy_sip_config_to_asterisk(host_ip: str, local_config_path: str, enterprise_id: str) -> dict:
    """Деплой sip_addproviders.conf на удаленный Asterisk хост"""
    
    # SSH параметры (константы)
    SSH_PORT = 5059
    SSH_USER = "root" 
    SSH_PASSWORD = "5atx9Ate@pbx"
    REMOTE_PATH = "/etc/asterisk/sip_addproviders.conf"
    
    logging.info(f"Starting SIP config deployment to Asterisk host {host_ip} for enterprise {enterprise_id}")
    
    try:
        # 1. Копируем файл на хост
        scp_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "scp", "-P", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            local_config_path, f"{SSH_USER}@{host_ip}:{REMOTE_PATH}"
        ]
        
        logging.info(f"Copying SIP config file to {host_ip}...")
        scp_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *scp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=30.0
        )
        
        scp_stdout, scp_stderr = await scp_result.communicate()
        
        if scp_result.returncode != 0:
            error_msg = scp_stderr.decode().strip() if scp_stderr else "Unknown SCP error"
            logging.error(f"SCP failed for {host_ip}: {error_msg}")
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
        logging.info(f"SIP config file successfully copied to {host_ip}")
        
        # 2. Перезагружаем SIP и диалплан
        reload_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "ssh", "-p", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            f"{SSH_USER}@{host_ip}",
            'asterisk -rx "sip reload" && asterisk -rx "dialplan reload"'
        ]
        
        logging.info(f"Reloading SIP and dialplan on {host_ip}...")
        reload_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *reload_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=30.0
        )
        
        reload_stdout, reload_stderr = await reload_result.communicate()
        
        if reload_result.returncode != 0:
            error_msg = reload_stderr.decode().strip() if reload_stderr else "Unknown reload error"
            logging.error(f"Asterisk SIP reload failed for {host_ip}: {error_msg}")
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
        logging.info(f"Asterisk SIP successfully reloaded on {host_ip}")
        logging.info(f"SIP reload output: {reload_stdout.decode().strip()}")
        
        return {"success": True, "message": "Конфигурация SIP обновлена"}
        
    except asyncio.TimeoutError:
        logging.error(f"Timeout while deploying SIP config to {host_ip}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error deploying SIP config to {host_ip}: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def deploy_greeting_files_to_asterisk(host_ip: str, local_files: list, enterprise_id: str) -> dict:
    """Деплой файлов приветствий на удаленный Asterisk хост"""
    
    # SSH параметры (константы)
    SSH_PORT = 5059
    SSH_USER = "root" 
    SSH_PASSWORD = "5atx9Ate@pbx"
    REMOTE_PATH = "/var/lib/asterisk/sounds/custom/"
    
    logging.info(f"Starting greeting files deployment to Asterisk host {host_ip} for enterprise {enterprise_id}")
    
    try:
        # 1. Создаем папку на удаленном хосте
        mkdir_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "ssh", "-p", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            f"{SSH_USER}@{host_ip}",
            f'mkdir -p {REMOTE_PATH}'
        ]
        
        logging.info(f"Creating directory {REMOTE_PATH} on {host_ip}...")
        mkdir_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *mkdir_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=30.0
        )
        
        mkdir_stdout, mkdir_stderr = await mkdir_result.communicate()
        
        if mkdir_result.returncode != 0:
            error_msg = mkdir_stderr.decode().strip() if mkdir_stderr else "Unknown mkdir error"
            logging.error(f"Failed to create directory on {host_ip}: {error_msg}")
        
        # 2. Копируем файлы на хост
        success_count = 0
        for local_file_path in local_files:
            if not Path(local_file_path).exists():
                logging.warning(f"File {local_file_path} does not exist, skipping")
                continue
                
            filename = Path(local_file_path).name
            
            scp_cmd = [
                "sshpass", "-p", SSH_PASSWORD,
                "scp", "-P", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
                local_file_path, f"{SSH_USER}@{host_ip}:{REMOTE_PATH}{filename}"
            ]
            
            logging.info(f"Copying greeting file {filename} to {host_ip}...")
            scp_result = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *scp_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ),
                timeout=30.0
            )
            
            scp_stdout, scp_stderr = await scp_result.communicate()
            
            if scp_result.returncode == 0:
                success_count += 1
                logging.info(f"Successfully copied greeting file {filename} to {host_ip}")
            else:
                error_msg = scp_stderr.decode().strip() if scp_stderr else "Unknown SCP error"
                logging.error(f"Failed to copy greeting file {filename} to {host_ip}: {error_msg}")
        
        if success_count == len(local_files):
            return {"success": True, "message": "Файлы приветствий успешно сохранены"}
        elif success_count > 0:
            return {"success": True, "message": f"Скопировано {success_count} из {len(local_files)} файлов приветствий"}
        else:
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
    except asyncio.TimeoutError:
        logging.error(f"Timeout while deploying greeting files to {host_ip}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error deploying greeting files to {host_ip}: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def deploy_music_files_to_asterisk(host_ip: str, local_files: list, enterprise_id: str) -> dict:
    """Деплой файлов музыки ожидания на удаленный Asterisk хост"""
    
    # SSH параметры (константы)
    SSH_PORT = 5059
    SSH_USER = "root" 
    SSH_PASSWORD = "5atx9Ate@pbx"
    BASE_REMOTE_PATH = "/var/lib/asterisk/"
    
    logging.info(f"Starting music files deployment to Asterisk host {host_ip} for enterprise {enterprise_id}")
    
    try:
        success_count = 0
        
        for file_info in local_files:
            local_file_path = file_info['local_path']
            internal_filename = file_info['internal_filename']
            
            if not Path(local_file_path).exists():
                logging.warning(f"File {local_file_path} does not exist, skipping")
                continue
            
            # Определяем папку на удаленном хосте (без .wav)
            folder_name = internal_filename.replace('.wav', '')
            remote_folder = f"{BASE_REMOTE_PATH}{folder_name}/"
            
            # 1. Создаем папку на удаленном хосте
            mkdir_cmd = [
                "sshpass", "-p", SSH_PASSWORD,
                "ssh", "-p", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
                f"{SSH_USER}@{host_ip}",
                f'mkdir -p {remote_folder}'
            ]
            
            logging.info(f"Creating directory {remote_folder} on {host_ip}...")
            mkdir_result = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *mkdir_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ),
                timeout=30.0
            )
            
            mkdir_stdout, mkdir_stderr = await mkdir_result.communicate()
            
            if mkdir_result.returncode != 0:
                error_msg = mkdir_stderr.decode().strip() if mkdir_stderr else "Unknown mkdir error"
                logging.error(f"Failed to create directory {remote_folder} on {host_ip}: {error_msg}")
                continue
            
            # 2. Копируем файл в папку
            scp_cmd = [
                "sshpass", "-p", SSH_PASSWORD,
                "scp", "-P", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
                local_file_path, f"{SSH_USER}@{host_ip}:{remote_folder}{internal_filename}"
            ]
            
            logging.info(f"Copying music file {internal_filename} to {remote_folder}...")
            scp_result = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *scp_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ),
                timeout=30.0
            )
            
            scp_stdout, scp_stderr = await scp_result.communicate()
            
            if scp_result.returncode == 0:
                success_count += 1
                logging.info(f"Successfully copied music file {internal_filename} to {host_ip}")
            else:
                error_msg = scp_stderr.decode().strip() if scp_stderr else "Unknown SCP error"
                logging.error(f"Failed to copy music file {internal_filename} to {host_ip}: {error_msg}")
        
        if success_count == len(local_files):
            return {"success": True, "message": "Файлы музыки ожидания успешно сохранены"}
        elif success_count > 0:
            return {"success": True, "message": f"Скопировано {success_count} из {len(local_files)} файлов музыки"}
        else:
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
    except asyncio.TimeoutError:
        logging.error(f"Timeout while deploying music files to {host_ip}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error deploying music files to {host_ip}: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def deploy_musiconhold_conf_to_asterisk(host_ip: str, local_config_path: str, enterprise_id: str) -> dict:
    """Деплой musiconhold.conf на удаленный Asterisk хост"""
    
    # SSH параметры (константы)
    SSH_PORT = 5059
    SSH_USER = "root" 
    SSH_PASSWORD = "5atx9Ate@pbx"
    REMOTE_PATH = "/etc/asterisk/musiconhold.conf"
    
    logging.info(f"Starting musiconhold.conf deployment to Asterisk host {host_ip} for enterprise {enterprise_id}")
    
    try:
        # 1. Копируем файл на хост
        scp_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "scp", "-P", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            local_config_path, f"{SSH_USER}@{host_ip}:{REMOTE_PATH}"
        ]
        
        logging.info(f"Copying musiconhold.conf to {host_ip}...")
        scp_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *scp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=30.0
        )
        
        scp_stdout, scp_stderr = await scp_result.communicate()
        
        if scp_result.returncode != 0:
            error_msg = scp_stderr.decode().strip() if scp_stderr else "Unknown SCP error"
            logging.error(f"SCP failed for musiconhold.conf to {host_ip}: {error_msg}")
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
        logging.info(f"musiconhold.conf successfully copied to {host_ip}")
        
        # 2. Перезагружаем музыку на удержании
        reload_cmd = [
            "sshpass", "-p", SSH_PASSWORD,
            "ssh", "-p", str(SSH_PORT), "-o", "StrictHostKeyChecking=no",
            f"{SSH_USER}@{host_ip}",
            'asterisk -rx "moh reload"'
        ]
        
        logging.info(f"Reloading music on hold on {host_ip}...")
        reload_result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *reload_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ),
            timeout=30.0
        )
        
        reload_stdout, reload_stderr = await reload_result.communicate()
        
        if reload_result.returncode != 0:
            error_msg = reload_stderr.decode().strip() if reload_stderr else "Unknown reload error"
            logging.error(f"MOH reload failed for {host_ip}: {error_msg}")
            return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
        
        logging.info(f"Music on hold successfully reloaded on {host_ip}")
        logging.info(f"MOH reload output: {reload_stdout.decode().strip()}")
        
        return {"success": True, "message": "Музыка ожидания успешно обновлена"}
        
    except asyncio.TimeoutError:
        logging.error(f"Timeout while deploying musiconhold.conf to {host_ip}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}
    except Exception as e:
        logging.error(f"Unexpected error deploying musiconhold.conf to {host_ip}: {str(e)}")
        return {"success": False, "message": "Нет связи с АТС, повторите попытку позже"}

async def _generate_musiconhold_conf(conn, enterprise_number: str) -> str:
    """
    Генерирует содержимое файла musiconhold.conf на основе данных из БД.
    """
    base_content = """
;
; Music on Hold -- Sample Configuration
;
[general]
;cachertclasses=yes
[default]
mode=files
directory=moh
""".strip()

    # Подключаемся к БД если нет подключения
    if conn is None:
        conn = await asyncpg.connect(DB_CONFIG)
        close_conn = True
    else:
        close_conn = False
    
    try:
        hold_files = await conn.fetch(
            "SELECT internal_filename FROM music_files WHERE enterprise_number = $1 AND file_type = 'hold'",
            enterprise_number
        )

        dynamic_parts = []
        for file in hold_files:
            if file['internal_filename'] and file['internal_filename'].endswith('.wav'):
                context_name = file['internal_filename'][:-4] # Убираем .wav
                part = f"""
[{context_name}]
mode=files
directory={context_name}
sort=random
""".strip()
                dynamic_parts.append(part)

        full_content = base_content + "\n\n" + "\n\n".join(dynamic_parts)
        return full_content
    finally:
        if close_conn and conn:
            await conn.close()

async def generate_sip_addproviders_conf(enterprise_id: str) -> str:
    """Генерирует содержимое файла sip_addproviders.conf на основе данных из БД"""
    
    conn = None
    try:
        conn = await asyncpg.connect(DB_CONFIG)
        content_parts = []
        
        # 1. GSM-линии (сортировка по line_id)
        try:
            gsm_lines = await conn.fetch(
                "SELECT line_id FROM gsm_lines WHERE enterprise_number = $1 ORDER BY line_id",
                enterprise_id
            )
            for line in gsm_lines:
                context = f"""
[{line['line_id']}]
host=dynamic
type=peer
secret=4bfX5XuefNp3aksfhj232
callgroup=1
pickupgroup=1
disallow=all
allow=ulaw
context=from-out-office
directmedia=no
nat=force_rport,comedia
qualify=8000
insecure=invite
defaultuser=s
""".strip()
                content_parts.append(context)
        except Exception as e:
            logging.error(f"Ошибка при получении GSM-линий для конфига: {e}", exc_info=True)

        # 2. Внутренние линии (сортировка по номеру)
        try:
            internal_lines = await conn.fetch(
                "SELECT phone_number, password FROM user_internal_phones WHERE enterprise_number = $1 ORDER BY phone_number::integer",
                enterprise_id
            )
            for line in internal_lines:
                context = f"""
[{line['phone_number']}]
host=dynamic
type=friend
secret={line['password']}
callgroup=1
pickupgroup=1
disallow=all
allow=ulaw
context=inoffice
directmedia=no
nat=force_rport,comedia
qualify=8000
insecure=invite
callerid={line['phone_number']}
defaultuser={line['phone_number']}
""".strip()
                content_parts.append(context)
        except Exception as e:
            logging.error(f"Ошибка при получении внутренних линий для конфига: {e}", exc_info=True)

        # 3. SIP-линии (сортировка по id)
        try:
            sip_lines = await conn.fetch(
                "SELECT line_name, info FROM sip_unit WHERE enterprise_number = $1 ORDER BY id",
                enterprise_id
            )
            for line in sip_lines:
                # Тело контекста берется напрямую из поля 'info'
                context = f"[{line['line_name']}]\n{line['info']}".strip()
                content_parts.append(context)
        except Exception as e:
            logging.error(f"Ошибка при получении SIP-линий для конфига: {e}", exc_info=True)

        return "\n\n".join(content_parts)
        
    except Exception as e:
        logging.error(f"Ошибка при генерации sip_addproviders.conf для предприятия {enterprise_id}: {e}", exc_info=True)
        return ""
    finally:
        if conn and not conn.is_closed():
            await conn.close()

def generate_dial_in_context(schema_id, node, nodes, edges, music_files_info, dialexecute_contexts_map, user_phones_map):
    """Генерирует диалплан для узла 'Звонок на список номеров' с учетом внешних номеров."""
    logging.info(f"Generating dial_in context for node {node['id']} in schema {schema_id}")
    context_name = generate_context_name(schema_id, node['id'])
    
    data = node.get('data', {})
    managers_flat = data.get('managers', [])
    logging.info(f"Node data: {data}")

    all_dial_parts = []
    all_log_numbers = []

    for m in managers_flat:
        user_id = m.get('userId')
        phone = m.get('phone', '').strip()

        if not phone:
            continue

        # Внутренний номер (короткий, из цифр) - обрабатывается всегда, независимо от наличия user_id
        if phone.isdigit() and len(phone) <= 4:
            all_dial_parts.append(f"SIP/{phone}")
            all_log_numbers.append(phone)
            continue

        # Внешний номер (все остальное) - требует userId для поиска маршрута
        if not user_id:
            logging.warning(f"External number '{phone}' in node {node['id']} has no userId and will be ignored.")
            continue

        # Ищем исходящий контекст для этого пользователя
        outgoing_context = None
        all_internal_for_user = user_phones_map.get(user_id, [])
        sorted_internal = sorted(all_internal_for_user)
        
        for internal_num in sorted_internal:
            if internal_num in dialexecute_contexts_map:
                outgoing_context = dialexecute_contexts_map[internal_num]
                logging.info(f"Found outgoing context '{outgoing_context}' for user {user_id} via internal number {internal_num}")
                break
        
        if outgoing_context:
            phone_to_dial = phone.lstrip('+')
            all_log_numbers.append(phone_to_dial)
            all_dial_parts.append(f"Local/{phone_to_dial}@{outgoing_context}")
        else:
            logging.warning(f"No outgoing context found for any internal numbers of user {user_id}. External number {phone} will be ignored.")

    if not all_dial_parts:
        logging.warning(f"No numbers to dial for node {node['id']}. Skipping context generation.")
        return ""

    dial_command_string = "&".join(all_dial_parts)
    log_command_string = "&".join(all_log_numbers)
    logging.info(f"Dial command string: {dial_command_string}")

    wait_time = data.get('waitingRings', 3) * 5
    music_data = data.get('holdMusic', {'type': 'default'})
    music_option = music_data.get('type', 'default')

    dial_options = "TtKk"
    if music_option == 'default':
        dial_options = "m" + dial_options
    elif music_option == 'custom':
        music_name = music_data.get('name')
        if music_name and music_name in music_files_info:
            internal_filename = music_files_info[music_name]['internal_filename'].replace('.wav', '')
            dial_options = f"m({internal_filename})" + dial_options
            # Используем internal_filename напрямую, а не создаем новый контекст

    lines = [
        f"[{context_name}]",
        "exten => _X.,1,Noop",
        f"same => n,Macro(incall_dial,${{Trunk}},{log_command_string})",
        f"same => n,Dial({dial_command_string},{wait_time},{dial_options})",
    ]

    target_node_id = get_target_node_id(edges, node['id'])
    if target_node_id:
        final_target_id = find_first_meaningful_node(target_node_id, nodes, edges)
        if final_target_id:
            target_context_name = generate_context_name(schema_id, final_target_id)
            lines.append(f"same => n,Goto({target_context_name},${{EXTEN}},1)")

    lines.append("same => n,Hangup")
    lines.extend([
        "exten => h,1,NoOp(Call is end)",
        'exten => h,n,Set(AGISIGHUP="no")',
        "exten => h,n,StopMixMonitor()",
        "same => n,Macro(incall_end,${Trunk})"
    ])
    
    return "\n".join(lines)

# --- Context Generators ---

def generate_pattern_check_context(schema_id, node, nodes, edges):
    """Генерирует диалплан для узла 'patternCheck'."""
    context_name = generate_context_name(schema_id, node['id'])
    lines = [
        f"[{context_name}]",
        f"exten => _X.,1,NoOp(To external from ${{CALLERID(num)}})",
        f"same => n,MixMonitor(${{UNIQUEID}}.wav)",
    ]

    child_edges = [edge for edge in edges if edge['source'] == node['id']]

    for edge in child_edges:
        target_id = edge.get('target')
        if not target_id: continue

        child_node = get_node_by_id(nodes, target_id)
        if not child_node: continue

        pattern_name = child_node.get('data', {}).get('label')
        
        pattern_data = next((p for p in node.get('data', {}).get('patterns', []) if p.get('name') == pattern_name), None)
        if not pattern_data or not pattern_data.get('shablon'):
            continue
            
        pattern_shablon = pattern_data['shablon']

        final_target_id = find_first_meaningful_node(target_id, nodes, edges)

        if final_target_id:
            target_context_name = generate_context_name(schema_id, final_target_id)
            lines.append(f'same => n,GotoIf($[{{REGEX("{pattern_shablon}" ${{EXTEN}})}}]?{target_context_name},${{EXTEN}},1)')
            
    return "\n".join(lines)


def generate_greeting_context(schema_id, node, nodes, edges, music_files_info):
    """Генерирует диалплан для узла 'greeting'."""
    greeting_data = node.get('data', {}).get('greetingFile')
    if not greeting_data or not greeting_data.get('name'):
        logging.info(f"Skipping empty greeting node {node['id']}.")
        return ""

    context_name = generate_context_name(schema_id, node['id'])
    display_name = greeting_data.get('name')

    # Ищем internal_filename в переданной информации, учитывая file_type = 'start'
    music_info = None
    for key, info in music_files_info.items():
        if key == display_name and info.get('file_type') == 'start':
            music_info = info
            break
    
    if not music_info or 'internal_filename' not in music_info:
        logging.warning(f"Could not find 'start' music file info for '{display_name}' in node {node['id']}. Skipping.")
        return ""

    internal_filename_no_ext = music_info['internal_filename'].replace('.wav', '')
    
    lines = [
        f"[{context_name}]",
        "exten => _X.,1,Noop",
        f"same => n,Playback(custom/{internal_filename_no_ext})"
    ]

    target_node_id = get_target_node_id(edges, node['id'])
    final_target_id = None
    if target_node_id:
        final_target_id = find_first_meaningful_node(target_node_id, nodes, edges)

    if final_target_id:
        target_context_name = generate_context_name(schema_id, final_target_id)
        lines.append(f"same => n,Goto({target_context_name},${{EXTEN}},1)")
    else:
        lines.append("same => n,Hangup")

    lines.extend([
        "exten => h,1,NoOp(Call is end)",
        'exten => h,n,Set(AGISIGHUP="no")',
        "exten => h,n,StopMixMonitor()",
        "same => n,Macro(incall_end,${Trunk})"
    ])
    
    return "\n".join(lines)

def generate_external_lines_context(schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info):
    """Генерирует диалплан для узла 'Внешние линии'."""
    context_name = generate_context_name(schema_id, node['id'])
    lines = [f"[{context_name}]", "exten => _X.,1,NoOp"]

    external_lines = node.get('data', {}).get('external_lines', [])
    
    if not external_lines:
        lines.append("same => n,Hangup()")
    else:
        for line in sorted(external_lines, key=lambda x: x.get('priority', 99)):
            line_id_full = line.get('line_id', '')

            if line_id_full.startswith('gsm_'):
                line_id = line_id_full.split('_', 1)[1]
                gsm_info = gsm_lines_info.get(line_id)
                if not gsm_info or 'prefix' not in gsm_info:
                    continue
                prefix = gsm_info['prefix']
                lines.append(f"same => n,Macro(outcall_dial,{line_id},${{EXTEN}})")
                lines.append(f"same => n,Dial(SIP/{line_id}/{prefix}${{EXTEN}},,tTkK)")

            elif line_id_full.startswith('sip_'):
                line_name = line_id_full.split('_', 1)[1]
                sip_info = sip_unit_info.get(line_name)
                if not sip_info:
                    continue

                prefix_str = sip_info.get('prefix')
                
                # Сценарий А: префикс с фигурными скобками
                if prefix_str and '{' in prefix_str and '}' in prefix_str:
                    match = re.match(r'([^\{]+)\{(\d+)\}', prefix_str)
                    if match:
                        prefix_part = match.group(1)
                        offset_val = int(match.group(2))
                        offset = 12 - offset_val
                        
                        lines.append(f"same => n,Macro(outcall_dial,{line_name},${{EXTEN}})")
                        lines.append(f"same => n,Set(CALLERID(num)={line_name})")
                        lines.append(f"same => n,Dial(SIP/{line_name}/{prefix_part}${{EXTEN:{offset}}},,tTkK)")
                # Сценарий Б: префикс отсутствует или простой
                else:
                    lines.append(f"same => n,Macro(outcall_dial,{line_name},${{EXTEN}})")
                    lines.append(f"same => n,Dial(SIP/{line_name}/${{EXTEN}},,tTkK)")

        lines.append("same => n,Hangup")
        lines.extend([
            "exten => h,1,NoOp(Call is end)",
            'exten => h,n,Set(AGISIGHUP="no")',
            "exten => h,n,StopMixMonitor()",
            "same => n,Macro(outcall_end,${Trunk})"
        ])
        
    return "\n".join(lines)

def generate_department_context(enterprise_id, dept):
    dept_num = dept['department_number']
    members = dept['members']
    if not members:
        return ""
    
    context_name = generate_department_context_name(enterprise_id, dept_num)
    # Using SIP as per user example for internal calls
    dial_members = "&".join([f"SIP/{m}" for m in members])
    
    lines = [
        f"[{context_name}]",
        "exten => _X.,1,Noop",
        f"same => n,Macro(incall_dial,${{Trunk}},{dial_members})",
        f"same => n,Dial({dial_members},,tTkK)",
        "same => n,Hangup",
        "exten => h,1,NoOp(Call is end)",
        'exten => h,n,Set(AGISIGHUP="no")',
        "exten => h,n,StopMixMonitor()",
        "same => n,Macro(incall_end,${Trunk})"
    ]
    return "\n".join(lines)

def generate_work_schedule_context(schema_id, node, nodes, edges):
    """
    Генерирует диалплан для узла 'График работы' строго по заданным правилам.
    """
    context_name = generate_context_name(schema_id, node['id'])
    
    lines = [
        f"[{context_name}]",
        "exten => _X.,1,NoOp",
    ]
    
    periods = node.get('data', {}).get('periods', [])
    source_edges = [edge for edge in edges if edge['source'] == node['id']]
    
    # Сортируем ребра, чтобы обеспечить детерминированный порядок.
    # Ручки периодов (sourceHandle) обычно именуются "0", "1", "2"...
    # Ручка нерабочего времени - 'no-match' или отсутствует.
    # Сортировка по строковому представлению поставит numerics первыми.
    source_edges.sort(key=lambda x: str(x.get('sourceHandle', 'z')))

    num_periods = len(periods)
    period_edges = source_edges[:num_periods]
    off_hours_edge = source_edges[num_periods] if len(source_edges) > num_periods else None

    day_map = {
        'пн': 'mon', 'вт': 'tue', 'ср': 'wed', 'чт': 'thu',
        'пт': 'fri', 'сб': 'sat', 'вс': 'sun'
    }

    # Генерация GotoIfTime для рабочих периодов
    if periods and period_edges:
        for i, period_data in enumerate(periods):
            # Проверяем, что для периода есть соответствующее ребро
            if i < len(period_edges):
                target_node_id = period_edges[i].get('target')
                if not target_node_id: continue
                
                final_target_id = find_first_meaningful_node(target_node_id, nodes, edges)
                if not final_target_id: continue
                
                target_context_name = generate_context_name(schema_id, final_target_id)
                time_from = period_data.get('startTime', '00:00')
                time_to = period_data.get('endTime', '23:59')
                time_range = f"{time_from}-{time_to}"

                # Обработка дней недели
                days_of_week = period_data.get('days', [])
                
                # Специальный случай для 'пн-пт'
                if set(days_of_week) == {'пн', 'вт', 'ср', 'чт', 'пт'}:
                    lines.append(f"same => n,GotoIfTime({time_range},mon-fri,1-31,jan-dec?{target_context_name},${{EXTEN}},1)")
                else:
                    for day_short in days_of_week:
                        day_eng = day_map.get(day_short.lower())
                        if day_eng:
                            lines.append(f"same => n,GotoIfTime({time_range},{day_eng},1-31,jan-dec?{target_context_name},${{EXTEN}},1)")
            else:
                break
    
    # Генерация Goto для нерабочего времени
    if off_hours_edge:
        off_hours_target_id = off_hours_edge.get('target')
        final_off_hours_target_id = find_first_meaningful_node(off_hours_target_id, nodes, edges)
        if final_off_hours_target_id:
            off_hours_context_name = generate_context_name(schema_id, final_off_hours_target_id)
            lines.append(f"same => n,Goto({off_hours_context_name},${{EXTEN}},1)")

    # Финальный Hangup
    lines.append("same => n,Hangup")
    
    return "\n".join(lines)

# --- Словарь генераторов ---
NODE_GENERATORS = {
    'patternCheck': generate_pattern_check_context,
    'greeting': generate_greeting_context,
    'externalLines': lambda schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info: generate_external_lines_context(schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info),
    'dialIn': generate_dial_in_context,
}

# --- Статические части конфига ---
def get_config_header(token):
    # "Symbol in symbol" from user's example
    return f"""[globals]
DIALOPTIONS = mtT
QUEUEOPTIONS= tTHh
RINGTIME = 30
OUTRINGTIME = 120
TRANSFER_CONTEXT=dialexecute
ID_TOKEN={token}

[default]
exten => s,1,NoOp(Qualify response)

[inoffice]
exten => _*5X,1,ParkedCall(default,${{EXTEN:2}})
exten => *65,1,Answer()
exten => *65,n,Playback(hello-world)
exten => *65,n,Playback(demo-congrats)
exten => *65,n,Echo()
exten => *65,n,Hangup()
exten => _X.,1,Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)
exten => _X.,n,MixMonitor(${{UNIQUEID}}.wav)
exten => _X.,n,Wait(1)
exten => _X.,n,Goto(dialexecute,${{EXTEN}},1)
exten => _X.,n,System(/bin/echo '${{STRFTIME(${{EPOCH}},,%d-%m-%Y-%H_%M)}}--${{CALLERID(num)}}--${{EXTEN}}' >>/var/log/asterisk/service)
exten => _X.,n,Answer()
exten => _X.,n,Goto(waitredirect,${{EXTEN}},1)
exten => _00XXX,1,Confbridge(${{EXTEN:2}})
exten => _01XXX,1,Chanspy(SIP/${{EXTEN:2}},bqw)
exten => _02XXX,1,Chanspy(SIP/${{EXTEN:2}},Bbqw)
exten => _07XXX,1,AGI(perexvat.php,${{EXTEN:2}}:${{CHANNEL}}:1)
exten => _07XXX,2,Hangup()
exten => _08XXX,1,AGI(perexvat.php,${{EXTEN:2}}:${{CHANNEL}}:0)
exten => _09XXX,1,Chanspy(SIP/${{EXTEN:2}},bq)
exten => 750,1,Confbridge(750)
exten => 0,1,Confbridge(${{DIALEDPEERNUMBER}})
exten => 555,1,Answer()
exten => 555,2,Echo()
exten => _[+]X.,1,Goto(dialexecute,${{EXTEN:1}},1)
exten => _00X.,1,Goto(dialexecute,${{EXTEN:2}},1)"""

def get_config_footer():
    # "Symbol in symbol" from user's example
    return """[playbackivr]
exten => _X.,1,Noop(start playback ivr ${FILEPLAYBACK} ${WAITEXTEN})
exten => _X.,2,Background(custom/${FILEPLAYBACK})
exten => _X.,3,WaitExten(${WAITEXTEN})
exten => _X.,4,Goto(waitredirect,${EXTEN},1)

[playback]
exten => _X.,1,Noop(Start Playback ${FILEPLAYBACK})
exten => _X.,2,Answer()
exten => _X.,3,Playback(custom/${FILEPLAYBACK})
exten => _X.,4,Goto(waitredirect,${EXTEN},1)

[waitredirect]
exten => _X.,1,NoOp(wait for redirect ${CHANNEL} - ${CALLERID(all)})
exten => _X.,2,Wait(10)
exten => _X.,3,Goto(apphangup,${EXTEN},1)

[apphangup]
exten => _X.,1,Hangup(17)

[appchanspy]
exten => _X.,1,NoOp(start chanspy ${SPYSTRING})
exten => _X.,2,ChanSpy(${SPYSTRING},qv(-1))

[appchanspywhisp]
exten => _X.,1,Noop(start chanspywhisp ${SPYSTRING})
exten => _X.,2,ChanSpy(${SPYSTRING},wqv(-1))

[appconfbridge]
exten => _X.,1,Noop(Start confernce - ${CONFSTRING})
exten => _X.,2,ConfBridge(${CONFSTRING})

[sip-providers]
exten => _X.,1,UserEvent(PROVIDERS:${CALLERID(num)}:${EXTEN})
exten => _X.,2,Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)
exten => _X.,3,Dial(SIP/180,,tTkK)
exten => s,1,UserEvent(PROVIDERS:)

[wapo]
exten => _9XX,1,Dial(Local/${EXTEN}@inoffice,,tTkK)
exten => _4XX,1,Dial(Local/${EXTEN}@inoffice,,tTkK)
exten => _XXX,1,Dial(SIP/${EXTEN},,tTkK)
exten => _XXXXXXXXXXX,1,NoOp(TRANK is: ${TRUNK})
same => n,Dial(SIP/0001302/2${EXTEN},,tTkK)
exten => 555,1,Answer
exten => 555,n,Echo()
exten => 0,1,NoOp(Conferenc)
same => n,DumpChan()
same => n,ConfBridge(${DIALEDPEERNUMBER})
same=>h,1,Wait(1)
exten => _08XXX,1,AGI(perexvat.php,${EXTEN:2}:${CHANNEL}:0)

[web-zapros]
exten => 1,1,Dial(${WHO},,tT)

;******************************Smart Redirection******************************************
;******************************Smart Redirection******************************************
#include extensions_custom.conf"""

@app.post("/generate_config")
async def generate_config(request: GenerateConfigRequest):
    enterprise_id = request.enterprise_id
    logging.info(f"--- Starting config generation for enterprise: {enterprise_id} ---")
    conn = None
    try:
        conn = await asyncpg.connect(DB_CONFIG)
        
        token = await conn.fetchval("SELECT name2 FROM enterprises WHERE number = $1", enterprise_id)
        if not token:
            raise HTTPException(status_code=404, detail=f"Enterprise '{enterprise_id}' not found")

        # --- Предварительная загрузка данных ---
        schema_records = await conn.fetch("SELECT * FROM dial_schemas WHERE enterprise_id = $1", enterprise_id)
        logging.info(f"Found {len(schema_records)} schemas for enterprise {enterprise_id}")
        department_records = await conn.fetch("SELECT d.number AS department_number, array_agg(uip.phone_number) AS members FROM departments d JOIN department_members dm ON d.id = dm.department_id JOIN user_internal_phones uip ON dm.internal_phone_id = uip.id WHERE d.enterprise_number = $1 GROUP BY d.id", enterprise_id)
        gsm_lines_records = await conn.fetch("SELECT * FROM gsm_lines WHERE enterprise_number = $1", enterprise_id)
        gsm_lines_info = {rec['line_id']: {'prefix': rec['prefix']} for rec in gsm_lines_records}
        sip_unit_records = await conn.fetch("SELECT * FROM sip_unit WHERE enterprise_number = $1", enterprise_id)
        sip_unit_info = {rec['line_name']: {'prefix': rec['prefix']} for rec in sip_unit_records}
        
        music_files_records = await conn.fetch("SELECT display_name, internal_filename, file_type FROM music_files WHERE enterprise_number = $1", enterprise_id)
        music_files_info = {
            r['display_name']: {
                'internal_filename': r['internal_filename'],
                'file_type': r['file_type']
            } for r in music_files_records
        }
        
        user_records = await conn.fetch("""
            SELECT u.id, array_agg(uip.phone_number) as internal_phones
            FROM users u
            LEFT JOIN user_internal_phones uip ON u.id = uip.user_id
            WHERE u.enterprise_number = $1 AND uip.phone_number IS NOT NULL
            GROUP BY u.id;
        """, enterprise_id)
        user_phones_map = {user['id']: user['internal_phones'] for user in user_records}

        # --- Этап 1: Генерация dialexecute и карты маршрутов для Local/
        dialexecute_contexts_map = {}
        for r in schema_records:
            if r.get('schema_type') != 'outgoing': continue
            schema = {"id": r['schema_id'], "data": json.loads(r['schema_data'])}
            start_node = get_node_by_id(schema['data']['nodes'], 'start-outgoing')
            if not start_node: continue
            managers = start_node.get('data', {}).get('phones', [])
            first_node_id = get_target_node_id(schema['data']['edges'], 'start-outgoing')
            if not managers or not first_node_id: continue
            target_context = generate_context_name(schema['id'], first_node_id)
            for phone in managers:
                dialexecute_contexts_map[phone] = target_context
        
        dialexecute_lines = [
            "[dialexecute]",
            "exten => _XXX,1,NoOp(Local call to ${EXTEN})",
            "same => n,Dial(SIP/${EXTEN},,tTkK)",
            "exten => _XXXX.,1,NoOp(Call to ${EXTEN} from ${CHANNEL(name):4:3}) and ${CALLERID(num)})",
        ]
        for phone, context in sorted(dialexecute_contexts_map.items(), key=lambda item: int(item[0])):
            dialexecute_lines.append(f'same => n,GotoIf($["${{CHANNEL(name):4:3}}" = "{phone}"]?{context},${{EXTEN}},1)')
            dialexecute_lines.append(f'same => n,GotoIf($["${{CALLERID(num)}}" = "{phone}"]?{context},${{EXTEN}},1) ')
        
        for dept in department_records:
            dept_num = dept['department_number']
            context_name = generate_department_context_name(enterprise_id, dept_num)
            dialexecute_lines.append(f"exten => {dept_num},1,Goto({context_name},${{EXTEN}},1)")

        dialexecute_lines.extend([
            "exten => _[+]X.,1,Goto(dialexecute,${{EXTEN:1}},1)",
            "exten => _00X.,1,Goto(dialexecute,${{EXTEN:2}},1)",
            "exten => h,1,NoOp(CALL=========================================================)",
            "same => n,Macro(localcall_end)",
            "same => n,NoOp(CALL======================================================END)",
        ])

        # --- Этап 2: Генерация всех контекстов с разделением ---
        pre_from_out_office_contexts = [generate_department_context(enterprise_id, dept) for dept in department_records]
        post_from_out_office_contexts = []
        
        # Обновленный NODE_GENERATORS
        NODE_GENERATORS_UPDATED = {
            'patternCheck': generate_pattern_check_context,
            'greeting': generate_greeting_context,
            'externalLines': generate_external_lines_context,
            'dial': generate_dial_in_context,
            'workSchedule': generate_work_schedule_context,
        }

        for r in schema_records:
            schema_id, data, schema_type = r['schema_id'], json.loads(r['schema_data']), r.get('schema_type', 'outgoing')
            logging.info(f"Processing schema_id: {schema_id}, schema_type: {schema_type}")
            nodes, edges = data['nodes'], data['edges']
            
            context_list = post_from_out_office_contexts if schema_type == 'incoming' else pre_from_out_office_contexts
            
            for node in nodes:
                node_type = node.get('type')
                node['enterprise_id'] = enterprise_id 
                
                if node_type == 'dial' and schema_type == 'incoming':
                     logging.info(f"Found 'dial' node ({node['id']}) in 'incoming' schema ({schema_id}). Calling generator.")

                if node_type in NODE_GENERATORS_UPDATED:
                    generator_func = NODE_GENERATORS_UPDATED[node_type]
                    context_str = None
                    if node_type == 'externalLines':
                        context_str = generator_func(schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info)
                    elif node_type == 'dial':
                        # Входящие dial-узлы используют свою логику с картой маршрутов
                        if schema_type == 'incoming':
                            context_str = generator_func(schema_id, node, nodes, edges, music_files_info, dialexecute_contexts_map, user_phones_map)
                    elif node_type == 'greeting':
                        context_str = generator_func(schema_id, node, nodes, edges, music_files_info)
                    elif node_type in ['patternCheck', 'workSchedule']:
                        context_str = generator_func(schema_id, node, nodes, edges)
                    
                    if context_str:
                        context_list.append(context_str)

        logging.info(f"Generated {len(pre_from_out_office_contexts)} pre-contexts and {len(post_from_out_office_contexts)} post-contexts.")

        # --- Этап 3: Генерация from-out-office ---
        from_out_office_lines = [
            "[from-out-office]",
            "exten => _X.,1,Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)",
            "same => n,Set(Trunk=${EXTEN})",
            "same => n,Answer",
            "same => n,Macro(incall_start,${Trunk})",
            "same => n,Set(CALLERID(num)=${CALLERID(name)})",
            "same => n,Set(CALLERID(name)=${NEWNAME}-${CALLERID(name)})",
            "same => n,Set(CDR(userfield)=${NEWNAME}-${CALLERID(name)})",
            "same => n,Answer",
            "exten => _X.,n,MixMonitor(${UNIQUEID}.wav)",
            "exten => _X.,n,NoOp(NOW is ${CALLERID(num)})"
        ]
        lines_with_context = []
        
        # GSM Lines
        incoming_gsm_lines = [line for line in gsm_lines_records if line['in_schema'] is not None]
        for line in sorted(incoming_gsm_lines, key=lambda x: int(x['line_id'])):
            schema = next((s for s in schema_records if s['schema_name'] == line['in_schema'] and s.get('schema_type') == 'incoming'), None)
            if schema:
                nodes, edges = json.loads(schema['schema_data'])['nodes'], json.loads(schema['schema_data'])['edges']
                start_node = get_node_by_id(nodes, '1')
                if start_node:
                    target_node_id = find_first_meaningful_node(start_node['id'], nodes, edges)
                    if target_node_id:
                        context_name = generate_context_name(schema['schema_id'], target_node_id)
                        lines_with_context.append({'line_id': line['line_id'], 'context': context_name})
        
        # SIP Lines
        incoming_sip_lines = [line for line in sip_unit_records if line['in_schema'] is not None]
        for line in sorted(incoming_sip_lines, key=lambda x: x['id']):
            schema = next((s for s in schema_records if s['schema_name'] == line['in_schema'] and s.get('schema_type') == 'incoming'), None)
            if schema:
                nodes, edges = json.loads(schema['schema_data'])['nodes'], json.loads(schema['schema_data'])['edges']
                start_node = get_node_by_id(nodes, '1')
                if start_node:
                    target_node_id = find_first_meaningful_node(start_node['id'], nodes, edges)
                    if target_node_id:
                        context_name = generate_context_name(schema['schema_id'], target_node_id)
                        lines_with_context.append({'line_id': line['line_name'], 'context': context_name})

        for item in lines_with_context:
            from_out_office_lines.append(f'exten => _X.,n,GotoIf($["${{EXTEN}}" = "{item["line_id"]}"]?{item["context"]},${{EXTEN}},1)')
        
        from_out_office_lines.extend([
            "exten => _X.,n,Hangup",
            "exten => h,1,NoOp(Call is end)",
            "exten => h,n,Set(AGISIGHUP=\"no\")",
            "exten => h,n,StopMixMonitor()",
            "same => n,Macro(incall_end,${Trunk})"
        ])

        # --- Этап 4: Финальная сборка ---
        config_parts = [
            get_config_header(token),
            "\n".join(dialexecute_lines),
        ]
        config_parts.extend(filter(None, pre_from_out_office_contexts))
        config_parts.append("\n".join(from_out_office_lines))
        config_parts.extend(filter(None, post_from_out_office_contexts))
        config_parts.append(get_config_footer())

        final_config = "\n\n".join(filter(None, config_parts))
        logging.info(f"Final config generated. Length: {len(final_config)}. Writing to file.")

        config_dir = Path(f"music/{enterprise_id}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "extensions.conf"
        with open(config_path, "w") as f:
            f.write(final_config)

        # --- Асинхронное развертывание на Asterisk хост ---
        deployment_result = {"success": False, "message": "Развертывание не выполнялось"}
        
        # Получаем IP хоста предприятия
        enterprise_ip = await conn.fetchval("SELECT ip FROM enterprises WHERE number = $1 AND is_enabled = true", enterprise_id)
        if enterprise_ip and enterprise_ip.strip():
            logging.info(f"Found enterprise IP: {enterprise_ip} for enterprise {enterprise_id}")
            # Запускаем развертывание асинхронно в фоне
            asyncio.create_task(deploy_config_to_asterisk(enterprise_ip, str(config_path), enterprise_id))
            deployment_result = {"success": True, "message": "Схемы звонков обновлены"}
        else:
            logging.warning(f"No IP found or enterprise disabled for enterprise {enterprise_id}")
            deployment_result = {"success": False, "message": "IP адрес АТС не настроен"}

        logging.info(f"--- Finished config generation for enterprise: {enterprise_id} ---")
        return {
            "message": "Config generated", 
            "path": str(config_path), 
            "config": final_config,
            "deployment": deployment_result
        }

    except Exception as e:
        logging.error(f"Error generating config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn and not conn.is_closed():
            await conn.close()

@app.post("/generate_sip_config")
async def generate_sip_config(request: GenerateConfigRequest):
    """Генерирует и разворачивает sip_addproviders.conf"""
    enterprise_id = request.enterprise_id
    logging.info(f"--- Starting SIP config generation for enterprise: {enterprise_id} ---")
    
    try:
        # Генерируем содержимое SIP конфига
        sip_config_content = await generate_sip_addproviders_conf(enterprise_id)
        
        if not sip_config_content.strip():
            logging.warning(f"Generated empty SIP config for enterprise {enterprise_id}")
            return {
                "message": "SIP config generated (empty)", 
                "deployment": {"success": False, "message": "Конфигурация пуста"}
            }
        
        # Записываем локальный файл
        config_dir = Path(f"music/{enterprise_id}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "sip_addproviders.conf"
        
        with open(config_path, "w") as f:
            f.write(sip_config_content)
        
        logging.info(f"SIP config file written to {config_path} ({len(sip_config_content)} bytes)")
        
        # Подключаемся к БД для получения IP хоста
        conn = await asyncpg.connect(DB_CONFIG)
        try:
            enterprise_ip = await conn.fetchval("SELECT ip FROM enterprises WHERE number = $1 AND is_enabled = true", enterprise_id)
            if enterprise_ip and enterprise_ip.strip():
                logging.info(f"Found enterprise IP: {enterprise_ip} for enterprise {enterprise_id}")
                # Запускаем развертывание
                deployment_result = await deploy_sip_config_to_asterisk(enterprise_ip, str(config_path), enterprise_id)
            else:
                logging.warning(f"No IP found or enterprise disabled for enterprise {enterprise_id}")
                deployment_result = {"success": False, "message": "IP адрес АТС не настроен"}
        finally:
            await conn.close()
        
        logging.info(f"--- Finished SIP config generation for enterprise: {enterprise_id} ---")
        return {
            "message": "SIP config generated", 
            "path": str(config_path), 
            "config": sip_config_content,
            "deployment": deployment_result
        }
        
    except Exception as e:
        logging.error(f"Error generating SIP config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/deploy_audio_files")
async def deploy_audio_files(request: GenerateConfigRequest):
    """Развертывает ВСЕ аудиофайлы предприятия на удаленный хост (кнопка Обновить)"""
    enterprise_id = request.enterprise_id
    logging.info(f"--- Starting full audio files deployment for enterprise: {enterprise_id} ---")
    
    try:
        # Получаем IP хоста из БД
        conn = await asyncpg.connect(DB_CONFIG)
        try:
            # Проверяем что предприятие включено
            enterprise_row = await conn.fetchrow("SELECT ip, is_enabled FROM enterprises WHERE number = $1", enterprise_id)
            if not enterprise_row:
                logging.error(f"Enterprise {enterprise_id} not found")
                return {"error": "Enterprise not found"}
            
            if not enterprise_row['is_enabled']:
                logging.warning(f"Enterprise {enterprise_id} is disabled, skipping deployment")
                return {
                    "message": "Audio files processed (enterprise disabled)", 
                    "deployment": {"success": False, "message": "Предприятие отключено"}
                }
                
            host_ip = enterprise_row['ip']
            if not host_ip:
                logging.error(f"No IP found for enterprise {enterprise_id}")
                return {"error": "No IP address configured for enterprise"}
            
            # Получаем все аудиофайлы из БД
            greetings_rows = await conn.fetch(
                "SELECT internal_filename FROM music_files WHERE enterprise_number = $1 AND file_type = 'start'", 
                enterprise_id
            )
            
            music_rows = await conn.fetch(
                "SELECT internal_filename FROM music_files WHERE enterprise_number = $1 AND file_type = 'hold'", 
                enterprise_id
            )
        finally:
            await conn.close()
        
        # Подготавливаем списки файлов
        greeting_files = []
        for row in greetings_rows:
            file_path = f"music/{enterprise_id}/start/{row['internal_filename']}"
            if Path(file_path).exists():
                greeting_files.append(file_path)
            else:
                logging.warning(f"Greeting file not found: {file_path}")
        
        music_files = []
        for row in music_rows:
            file_path = f"music/{enterprise_id}/hold/{row['internal_filename']}"
            if Path(file_path).exists():
                music_files.append({
                    'local_path': file_path,
                    'internal_filename': row['internal_filename']
                })
            else:
                logging.warning(f"Music file not found: {file_path}")
        
        # Результаты развертывания
        greeting_result = {"success": True, "message": "Нет файлов приветствий"}
        music_result = {"success": True, "message": "Нет файлов музыки"}
        musiconhold_result = {"success": True, "message": "Конфиг музыки не требуется"}
        
        # 1. Развертываем файлы приветствий
        if greeting_files:
            greeting_result = await deploy_greeting_files_to_asterisk(host_ip, greeting_files, enterprise_id)
        
        # 2. Развертываем файлы музыки
        if music_files:
            music_result = await deploy_music_files_to_asterisk(host_ip, music_files, enterprise_id)
            
            # 3. Генерируем и развертываем musiconhold.conf
            if music_result['success']:
                musiconhold_content = await _generate_musiconhold_conf(None, enterprise_id)
                
                if musiconhold_content.strip():
                    # Записываем локальный файл
                    config_dir = Path("music") / enterprise_id
                    config_dir.mkdir(parents=True, exist_ok=True)
                    musiconhold_path = config_dir / "musiconhold.conf"
                    
                    with open(musiconhold_path, "w") as f:
                        f.write(musiconhold_content)
                    
                    logging.info(f"musiconhold.conf written ({len(musiconhold_content)} bytes) to {musiconhold_path}")
                    
                    # Развертываем на хост
                    musiconhold_result = await deploy_musiconhold_conf_to_asterisk(host_ip, str(musiconhold_path), enterprise_id)
        
        # Формируем итоговый результат
        all_success = greeting_result['success'] and music_result['success'] and musiconhold_result['success']
        
        if all_success:
            combined_message = "Все аудиофайлы успешно обновлены"
        else:
            failed_parts = []
            if not greeting_result['success']:
                failed_parts.append("приветствия")
            if not music_result['success']:
                failed_parts.append("музыка")
            if not musiconhold_result['success']:
                failed_parts.append("конфигурация")
            
            combined_message = f"Ошибки при обновлении: {', '.join(failed_parts)}"
        
        return {
            "message": "Audio files deployment completed", 
            "deployment": {
                "success": all_success, 
                "message": combined_message,
                "details": {
                    "greetings": greeting_result,
                    "music": music_result,
                    "musiconhold": musiconhold_result
                }
            }
        }
        
    except Exception as e:
        logging.error(f"Error deploying audio files for enterprise {enterprise_id}: {str(e)}")
        return {"error": f"Failed to deploy audio files: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006, log_config="log_config.json")