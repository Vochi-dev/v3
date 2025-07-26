#!/usr/bin/env python3
"""
Сервис автоматической синхронизации данных с удаленных Asterisk серверов
Порт: 8007
"""

import asyncio
import json
import subprocess
import psycopg2
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import logging
from telegram import Bot
from telegram.error import BadRequest

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Asterisk Download Service",
    description="Сервис синхронизации данных с удаленных Asterisk серверов",
    version="1.0.0"
)

# Фоновая задача автоматической синхронизации
async def auto_sync_task():
    """Автоматическая синхронизация live событий каждые N минут"""
    while True:
        try:
            logger.info("Запуск автоматической синхронизации live событий")
            results = await sync_live_events()
            
            total_new = sum(stats.new_events for stats in results.values())
            if total_new > 0:
                logger.info(f"Автосинхронизация: добавлено {total_new} новых событий")
            else:
                logger.info("Автосинхронизация: новых событий не найдено")
                
        except Exception as e:
            logger.error(f"Ошибка автоматической синхронизации: {e}")
        
        # Ждем следующий интервал
        await asyncio.sleep(AUTO_SYNC_INTERVAL * 60)

@app.on_event("startup")
async def startup_event():
    """Запуск фонового задания при старте приложения"""
    logger.info(f"Запуск сервиса загрузки, автосинхронизация каждые {AUTO_SYNC_INTERVAL} минут")
    asyncio.create_task(auto_sync_task())

# SSH конфигурация (общая для всех серверов)
SSH_CONFIG = {
    "ssh_port": "5059",
    "ssh_password": "5atx9Ate@pbx"
}

def get_active_enterprises() -> Dict[str, Dict]:
    """Получить список активных предприятий из базы данных"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT number, name, host, secret, ip 
            FROM enterprises 
            WHERE is_enabled = true AND active = true
            ORDER BY number
        """)
        
        enterprises = {}
        for row in cursor.fetchall():
            enterprises[row[0]] = {  # number как ключ
                "name": row[1],
                "host": row[2],
                "token": row[3],  # secret используется как token
                "ip": row[4],
                "ssh_port": SSH_CONFIG["ssh_port"],
                "ssh_password": SSH_CONFIG["ssh_password"]
            }
        
        conn.close()
        return enterprises
        
    except Exception as e:
        logger.error(f"Ошибка получения списка предприятий: {e}")
        return {}

# Интервал автоматической синхронизации (в минутах)
AUTO_SYNC_INTERVAL = 5

# PostgreSQL настройки
PG_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "database": "postgres", 
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg=="
}

class SyncStats(BaseModel):
    enterprise_id: str
    total_downloaded: int
    new_events: int
    failed_events: int
    last_sync: Optional[datetime]
    status: str

class DownloadRequest(BaseModel):
    enterprise_id: str
    force_all: bool = False
    date_from: Optional[str] = None
    date_to: Optional[str] = None

# Глобальная переменная для отслеживания активных задач
active_tasks: Dict[str, bool] = {}

def get_db_connection():
    """Получить соединение с PostgreSQL"""
    return psycopg2.connect(**PG_CONFIG)

def get_remote_hangup_events(enterprise_id: str, db_file: str) -> List[Dict]:
    """Получить события hangup из удаленного SQLite файла (обычная таблица APIlogs)"""
    enterprises = get_active_enterprises()
    config = enterprises.get(enterprise_id)
    if not config:
        raise ValueError(f"Конфигурация для предприятия {enterprise_id} не найдена")
    
    cmd = f'sshpass -p "{config["ssh_password"]}" ssh -p {config["ssh_port"]} -o StrictHostKeyChecking=no root@{config["ip"]} \'sqlite3 {db_file} "SELECT DateTime, Uniqueid, request FROM APIlogs WHERE event = \\"hangup\\" ORDER BY DateTime;"\''
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error(f"Ошибка выполнения команды: {result.stderr}")
            return []
        
        events = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split('|', 2)
                if len(parts) == 3:
                    datetime_str, unique_id, request_json = parts
                    try:
                        request_data = json.loads(request_json)
                        events.append({
                            'datetime': datetime_str,
                            'unique_id': unique_id,
                            'data': request_data
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"Ошибка парсинга JSON для {unique_id}: {e}")
        
        return events
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут при получении данных из {db_file}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении данных из {db_file}: {e}")
        return []

def get_remote_failed_hangup_events(enterprise_id: str, db_file: str) -> List[Dict]:
    """Получить неуспешные события hangup из удаленного SQLite файла (AlternativeAPIlogs)"""
    enterprises = get_active_enterprises()
    config = enterprises.get(enterprise_id)
    if not config:
        raise ValueError(f"Конфигурация для предприятия {enterprise_id} не найдена")
    
    # Ищем в таблице AlternativeAPIlogs события hangup со статусом НЕ успешным (не <Response [200]>)
    cmd = f'sshpass -p "{config["ssh_password"]}" ssh -p {config["ssh_port"]} -o StrictHostKeyChecking=no root@{config["ip"]} \'sqlite3 {db_file} "SELECT DateTime, Uniqueid, request, status, response FROM AlternativeAPIlogs WHERE event = \\"hangup\\" AND (status IS NULL OR status NOT LIKE \\"<Response [200]>%\\") ORDER BY DateTime;"\''
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error(f"Ошибка выполнения команды: {result.stderr}")
            return []
        
        events = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split('|')
                if len(parts) >= 3:
                    datetime_str = parts[0]
                    unique_id = parts[1] 
                    request_json = parts[2]
                    status = parts[3] if len(parts) > 3 else None
                    response = parts[4] if len(parts) > 4 else None
                    
                    try:
                        request_data = json.loads(request_json)
                        events.append({
                            'datetime': datetime_str,
                            'unique_id': unique_id,
                            'data': request_data,
                            'status': status,
                            'response': response
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"Ошибка парсинга JSON для {unique_id}: {e}")
        
        return events
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут при получении данных из {db_file}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении данных из {db_file}: {e}")
        return []

def get_remote_db_files(enterprise_id: str, date_from: str = None, date_to: str = None) -> List[str]:
    """Получить список файлов логов с удаленного сервера"""
    enterprises = get_active_enterprises()
    config = enterprises.get(enterprise_id)
    if not config:
        return []
    
    cmd = f'sshpass -p "{config["ssh_password"]}" ssh -p {config["ssh_port"]} -o StrictHostKeyChecking=no root@{config["ip"]} \'ls -1 /var/log/asterisk/Listen_AMI_*.db\''
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.error(f"Ошибка получения списка файлов: {result.stderr}")
            return []
        
        files = []
        for line in result.stdout.strip().split('\n'):
            if line and 'Listen_AMI_' in line:
                # Фильтрация по датам если указаны
                if date_from or date_to:
                    try:
                        # Извлекаем дату из имени файла Listen_AMI_2025-06-22.db
                        file_date = line.split('Listen_AMI_')[1].split('.db')[0]
                        if date_from and file_date < date_from:
                            continue
                        if date_to and file_date > date_to:
                            continue
                    except:
                        continue
                files.append(line.strip())
        
        return sorted(files)
    except Exception as e:
        logger.error(f"Ошибка при получении списка файлов: {e}")
        return []

def parse_call_data(event: Dict, enterprise_id: str) -> Dict:
    """Парсинг данных звонка"""
    data = event['data']
    enterprises = get_active_enterprises()
    config = enterprises[enterprise_id]
    
    # ИСПРАВЛЕНО: CallType и CallStatus остаются как цифры, как в hangup.py
    call_type = str(data.get('CallType', '0'))  # Оставляем как строку цифры
    call_status = str(data.get('CallStatus', '0'))  # Оставляем как строку цифры
    
    # Вычисляем длительность
    try:
        start_time = datetime.fromisoformat(data.get('StartTime', ''))
        end_time = datetime.fromisoformat(data.get('EndTime', ''))
        duration = int((end_time - start_time).total_seconds())
    except:
        duration = 0
    
    # Основной участник (первый из Extensions)
    extensions = data.get('Extensions', [])
    main_extension = extensions[0] if extensions else None
    
    # 🔗 Генерируем UUID ссылку для recovery события
    uuid_token = str(uuid.uuid4())
    call_url = f"https://bot.vochi.by/recordings/file/{uuid_token}"
    
    return {
        'unique_id': data.get('UniqueId'),
        'enterprise_id': enterprise_id,
        'token': config['token'],
        'start_time': data.get('StartTime'),
        'end_time': data.get('EndTime'),
        'duration': duration,
        'phone_number': data.get('Phone'),
        'trunk': data.get('Trunk'),
        'main_extension': main_extension,
        'extensions_count': len(extensions),
        'call_type': call_type,  # ИСПРАВЛЕНО: теперь цифра как в hangup.py
        'call_status': call_status,  # ИСПРАВЛЕНО: теперь цифра как в hangup.py
        'data_source': 'recovery',
        'asterisk_host': config['ip'],
        'raw_data': json.dumps(data),
        'extensions': extensions,
        'uuid_token': uuid_token,
        'call_url': call_url
    }

def insert_call_to_db(cursor, call_data: Dict) -> Optional[int]:
    """Вставка звонка в БД"""
    insert_call_sql = """
    INSERT INTO calls (
        unique_id, enterprise_id, token, start_time, end_time, duration,
        phone_number, trunk, main_extension, extensions_count,
        call_type, call_status, data_source, asterisk_host, raw_data,
        uuid_token, call_url
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    ) 
    ON CONFLICT (unique_id) DO NOTHING
    RETURNING id;
    """
    
    cursor.execute(insert_call_sql, (
        call_data['unique_id'],
        call_data['enterprise_id'],
        call_data['token'],
        call_data['start_time'],
        call_data['end_time'],
        call_data['duration'],
        call_data['phone_number'],
        call_data['trunk'],
        call_data['main_extension'],
        call_data['extensions_count'],
        call_data['call_type'],
        call_data['call_status'],
        call_data['data_source'],
        call_data['asterisk_host'],
        call_data['raw_data'],
        call_data['uuid_token'],
        call_data['call_url']
    ))
    
    result = cursor.fetchone()
    return result[0] if result else None

def insert_participants_to_db(cursor, call_id: int, extensions: List[str], call_data: Dict):
    """Вставка участников звонка"""
    if not call_id:
        return
    
    insert_participant_sql = """
    INSERT INTO call_participants (
        call_id, extension, participant_status, ring_order,
        ring_duration, dial_start, answer_time, hangup_time
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s
    ) 
    ON CONFLICT (call_id, extension) DO NOTHING;
    """
    
    for i, extension in enumerate(extensions):
        # Для answered звонков первый участник ответил
        if call_data['call_status'] == 'answered' and i == 0:
            participant_status = 'answered'
            answer_time = call_data['start_time']
        else:
            participant_status = call_data['call_status']  # no_answer, busy, etc.
            answer_time = None
        
        cursor.execute(insert_participant_sql, (
            call_id,
            extension,
            participant_status,
            i + 1,  # ring_order
            call_data['duration'],
            call_data['start_time'],  # dial_start
            answer_time,
            call_data['end_time']  # hangup_time
        ))

def update_sync_stats(cursor, enterprise_id: str, total_downloaded: int, new_events: int, failed_events: int):
    """Обновить статистику синхронизации"""
    active_enterprises = get_active_enterprises()
    config = active_enterprises[enterprise_id]
    
    upsert_sql = """
    INSERT INTO download_sync (
        enterprise_id, asterisk_host, total_downloaded_events, 
        last_successful_sync, updated_at
    ) VALUES (
        %s, %s, %s, %s, %s
    )
    ON CONFLICT (enterprise_id, asterisk_host) 
    DO UPDATE SET
        total_downloaded_events = download_sync.total_downloaded_events + EXCLUDED.total_downloaded_events,
        failed_events_count = %s,
        last_successful_sync = EXCLUDED.last_successful_sync,
        updated_at = EXCLUDED.last_successful_sync;
    """
    
    now = datetime.now()
    cursor.execute(upsert_sql, (
        enterprise_id,
        config['host'],
        new_events,
        now,
        now,
        failed_events
    ))

def get_telegram_settings(enterprise_id: str) -> Optional[Dict[str, str]]:
    """Получить настройки Telegram для предприятия"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT bot_token, chat_id 
            FROM enterprises 
            WHERE number = %s AND is_enabled = true
        """, (enterprise_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0] and result[1]:
            return {
                "bot_token": result[0],
                "chat_id": result[1]
            }
        else:
            logger.warning(f"Настройки Telegram для предприятия {enterprise_id} не найдены или неполные")
            return None
            
    except Exception as e:
        logger.error(f"Ошибка получения настроек Telegram для {enterprise_id}: {e}")
        return None

def format_phone_number(phone: str) -> str:
    """Форматирование номера телефона для отображения"""
    if not phone or phone == "":
        return "Номер не определен"
    
    # Убираем лишние символы
    clean_phone = ''.join(filter(str.isdigit, phone))
    
    if len(clean_phone) == 11 and clean_phone.startswith('8'):
        # Российский номер в формате 8XXXXXXXXXX
        return f"+7{clean_phone[1:]}"
    elif len(clean_phone) == 10:
        # Номер без кода страны
        return f"+7{clean_phone}"
    elif len(clean_phone) == 12 and clean_phone.startswith('375'):
        # Белорусский номер
        return f"+{clean_phone}"
    elif clean_phone.startswith('7') and len(clean_phone) == 11:
        # Номер уже с кодом +7
        return f"+{clean_phone}"
    else:
        return f"+{clean_phone}" if clean_phone else "Номер не определен"

def is_internal_number(number: str) -> bool:
    """Проверка, является ли номер внутренним"""
    if not number:
        return False
    clean_number = ''.join(filter(str.isdigit, number))
    return len(clean_number) <= 4 and clean_number.isdigit()

async def send_recovery_telegram_message(call_data: Dict, enterprise_id: str):
    """Отправка сообщения в Telegram о recovery событии"""
    try:
        # Получаем настройки Telegram
        telegram_settings = get_telegram_settings(enterprise_id)
        if not telegram_settings:
            logger.warning(f"Не удалось получить настройки Telegram для {enterprise_id}")
            return False
        
        # Создаем бота
        bot = Bot(token=telegram_settings["bot_token"])
        chat_id = telegram_settings["chat_id"]
        
        # Формируем сообщение согласно формату из hangup.py
        phone_number = call_data.get('phone_number', '')
        call_type = int(call_data.get('call_type', '0'))  # ИСПРАВЛЕНО: приводим к int
        call_status = int(call_data.get('call_status', '0'))  # ИСПРАВЛЕНО: приводим к int
        duration = call_data.get('duration', 0)
        start_time = call_data.get('start_time', '')
        main_extension = call_data.get('main_extension', '')
        call_url = call_data.get('call_url', '')
        trunk = call_data.get('trunk', '')
        
        # ИСПРАВЛЕНО: Правильная логика как в hangup.py
        # CallType: 0 = входящий, 1 = исходящий, 2 = внутренний
        # CallStatus: 2 = успешный, остальные = неуспешный
        is_incoming = call_type == 0
        is_outgoing = call_type == 1
        is_internal = call_type == 2
        is_answered = call_status == 2
        
        # Форматируем номер
        formatted_phone = format_phone_number(phone_number)
        
        # Форматируем длительность
        duration_text = f"{duration//60:02d}:{duration%60:02d}" if duration > 0 else "00:00"
        
        # Форматируем время начала
        time_part = "неизв"
        if start_time:
            try:
                if 'T' in start_time:
                    time_part = start_time.split('T')[1][:5]
                elif ' ' in start_time:
                    parts = start_time.split(' ')
                    if len(parts) >= 2:
                        time_part = parts[1][:5]
            except:
                pass
        
        # Формируем текст сообщения
        if is_internal:
            # Внутренние звонки
            if is_answered:
                text = f"🔄 Восстановленный успешный внутренний звонок\n☎️{main_extension}➡️\n☎️{formatted_phone}"
            else:
                text = f"🔄 Восстановленный неуспешный внутренний звонок\n☎️{main_extension}➡️\n☎️{formatted_phone}"
        elif is_incoming:
            # Входящие звонки
            if is_answered:
                text = f"🔄 Восстановленный входящий звонок\n💰{formatted_phone}"
                if main_extension and is_internal_number(main_extension):
                    text += f"\n☎️{main_extension}"
                if trunk:
                    text += f"\nЛиния: {trunk}"
                text += f"\n⏰Начало звонка {time_part}"
                text += f"\n⌛ Длительность: {duration_text}"
                if call_url:
                    text += f'\n🔉<a href="{call_url}">Запись разговора</a>'
            else:
                text = f"🔄 Восстановленный пропущенный входящий\n💰{formatted_phone}"
                if main_extension and is_internal_number(main_extension):
                    text += f"\n☎️{main_extension}"
                if trunk:
                    text += f"\nЛиния: {trunk}"
                text += f"\n⏰Начало звонка {time_part}"
                text += f"\n⌛ Дозванивался: {duration_text}"
        else:
            # Исходящие звонки
            if is_answered:
                text = f"🔄 Восстановленный исходящий звонок"
                if main_extension and is_internal_number(main_extension):
                    text += f"\n☎️{main_extension}"
                text += f"\n💰{formatted_phone}"
                if trunk:
                    text += f"\nЛиния: {trunk}"
                text += f"\n⏰Начало звонка {time_part}"
                text += f"\n⌛ Длительность: {duration_text}"
                if call_url:
                    text += f'\n🔉<a href="{call_url}">Запись разговора</a>'
            else:
                text = f"🔄 Восстановленный неуспешный исходящий"
                if main_extension and is_internal_number(main_extension):
                    text += f"\n☎️{main_extension}"
                text += f"\n💰{formatted_phone}"
                if trunk:
                    text += f"\nЛиния: {trunk}"
                text += f"\n⏰Начало звонка {time_part}"
                text += f"\n⌛ Дозванивался: {duration_text}"
        
        # Отправляем сообщение
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )
        
        logger.info(f"✅ Отправлено Telegram сообщение для {call_data['unique_id']} в чат {chat_id}")
        return True
        
    except BadRequest as e:
        logger.error(f"Ошибка отправки в Telegram для {enterprise_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Критическая ошибка отправки в Telegram для {enterprise_id}: {e}")
        return False

async def sync_live_events(enterprise_id: str = None) -> Dict[str, SyncStats]:
    """Синхронизация live событий (AlternativeAPIlogs со статусом НЕ ok)"""
    results = {}
    active_enterprises = get_active_enterprises()
    enterprises = [enterprise_id] if enterprise_id else active_enterprises.keys()
    
    for ent_id in enterprises:
        logger.info(f"Начинаю синхронизацию live событий для предприятия {ent_id}")
        
        # Берем файл сегодняшней даты
        today = datetime.now().strftime('%Y-%m-%d')
        db_file = f"/var/log/asterisk/Listen_AMI_{today}.db"
        
        try:
            # Получаем неуспешные события hangup
            events = get_remote_failed_hangup_events(ent_id, db_file)
            
            total_downloaded = len(events)
            new_events = 0
            failed_events = 0
            
            logger.info(f"Найдено {total_downloaded} неуспешных событий hangup для {ent_id}")
            
            if events:
                
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        for event in events:
                            try:
                                unique_id = event['unique_id']
                                logger.info(f"Обрабатываю событие {unique_id}")
                                
                                # Проверяем, нет ли уже такого события
                                cursor.execute(
                                    "SELECT id FROM calls WHERE unique_id = %s",
                                    (unique_id,)
                                )
                                existing = cursor.fetchone()
                                if existing:
                                    logger.info(f"Событие {unique_id} уже есть в БД")
                                    continue  # Уже есть в БД
                                
                                # Парсим и вставляем
                                call_data = parse_call_data(event, ent_id)
                                
                                call_id = insert_call_to_db(cursor, call_data)
                                if call_id:
                                    insert_participants_to_db(cursor, call_id, call_data['extensions'], call_data)
                                    new_events += 1
                                    logger.info(f"✅ Создана recovery запись call_id={call_id} для {unique_id}")
                                    logger.info(f"🔗 UUID ссылка: {call_data['call_url']}")
                                    
                                    # 📧 Отправляем уведомление в Telegram
                                    try:
                                        telegram_sent = await send_recovery_telegram_message(call_data, ent_id)
                                        if telegram_sent:
                                            logger.info(f"📱 Telegram уведомление отправлено для {unique_id}")
                                        else:
                                            logger.warning(f"📱 Не удалось отправить Telegram уведомление для {unique_id}")
                                    except Exception as telegram_error:
                                        logger.error(f"📱 Ошибка отправки Telegram уведомления для {unique_id}: {telegram_error}")
                                    
                                else:
                                    logger.warning(f"Не удалось вставить событие {unique_id}")
                                
                                conn.commit()
                                
                            except Exception as e:
                                logger.error(f"Ошибка обработки события {event.get('unique_id', 'unknown')}: {e}")
                                failed_events += 1
                        
                        # Обновляем статистику
                        update_sync_stats(cursor, ent_id, total_downloaded, new_events, failed_events)
                        conn.commit()
            
            results[ent_id] = SyncStats(
                enterprise_id=ent_id,
                total_downloaded=total_downloaded,
                new_events=new_events,
                failed_events=failed_events,
                last_sync=datetime.now(),
                status="success"
            )
            
            logger.info(f"Синхронизация live событий для {ent_id} завершена: {new_events} новых событий")
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации live событий для {ent_id}: {e}")
            results[ent_id] = SyncStats(
                enterprise_id=ent_id,
                total_downloaded=0,
                new_events=0,
                failed_events=0,
                last_sync=datetime.now(),
                status=f"error: {str(e)}"
            )
    
    return results

async def sync_enterprise_data(enterprise_id: str, force_all: bool = False, 
                              date_from: str = None, date_to: str = None) -> SyncStats:
    """Синхронизация данных предприятия"""
    if enterprise_id in active_tasks:
        raise HTTPException(status_code=409, detail=f"Синхронизация предприятия {enterprise_id} уже выполняется")
    
    active_tasks[enterprise_id] = True
    
    try:
        logger.info(f"Начинаем синхронизацию предприятия {enterprise_id}")
        
        # Получаем список файлов
        db_files = get_remote_db_files(enterprise_id, date_from, date_to)
        if not db_files:
            logger.warning(f"Файлы логов для предприятия {enterprise_id} не найдены")
            return SyncStats(
                enterprise_id=enterprise_id,
                total_downloaded=0,
                new_events=0,
                failed_events=0,
                last_sync=datetime.now(),
                status="no_files"
            )
        
        total_downloaded = 0
        new_events = 0
        failed_events = 0
        
        # Подключение к БД
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for db_file in db_files:
            logger.info(f"Обрабатываем файл {db_file}")
            
            events = get_remote_hangup_events(enterprise_id, db_file)
            file_new_events = 0
            file_failed_events = 0
            
            for event in events:
                try:
                    call_data = parse_call_data(event, enterprise_id)
                    call_id = insert_call_to_db(cursor, call_data)
                    
                    if call_id:
                        insert_participants_to_db(cursor, call_id, call_data['extensions'], call_data)
                        file_new_events += 1
                        logger.info(f"✅ Создана recovery запись call_id={call_id} для {call_data['unique_id']}")
                        logger.info(f"🔗 UUID ссылка: {call_data['call_url']}")
                        
                        # 📧 Отправляем уведомление в Telegram (только для новых записей)
                        try:
                            telegram_sent = await send_recovery_telegram_message(call_data, enterprise_id)
                            if telegram_sent:
                                logger.info(f"📱 Telegram уведомление отправлено для {call_data['unique_id']}")
                            else:
                                logger.warning(f"📱 Не удалось отправить Telegram уведомление для {call_data['unique_id']}")
                        except Exception as telegram_error:
                            logger.error(f"📱 Ошибка отправки Telegram уведомления для {call_data['unique_id']}: {telegram_error}")
                            
                    # Если call_id is None, значит запись уже существует (ON CONFLICT DO NOTHING)
                    
                except Exception as e:
                    logger.error(f"Ошибка при обработке события {event['unique_id']}: {e}")
                    file_failed_events += 1
                    continue
            
            total_downloaded += len(events)
            new_events += file_new_events
            failed_events += file_failed_events
            
            conn.commit()
            logger.info(f"Файл {db_file}: обработано {len(events)}, новых {file_new_events}, ошибок {file_failed_events}")
        
        # Обновляем статистику
        update_sync_stats(cursor, enterprise_id, total_downloaded, new_events, failed_events)
        conn.commit()
        
        conn.close()
        
        logger.info(f"Синхронизация предприятия {enterprise_id} завершена: обработано {total_downloaded}, новых {new_events}, ошибок {failed_events}")
        
        return SyncStats(
            enterprise_id=enterprise_id,
            total_downloaded=total_downloaded,
            new_events=new_events,
            failed_events=failed_events,
            last_sync=datetime.now(),
            status="success"
        )
        
    except Exception as e:
        logger.error(f"Критическая ошибка при синхронизации предприятия {enterprise_id}: {e}")
        return SyncStats(
            enterprise_id=enterprise_id,
            total_downloaded=0,
            new_events=0,
            failed_events=0,
            last_sync=datetime.now(),
            status=f"error: {str(e)}"
        )
    finally:
        active_tasks.pop(enterprise_id, None)

@app.get("/")
async def root():
    """Информация о сервисе"""
    active_enterprises = get_active_enterprises()
    return {
        "service": "Asterisk Download Service",
        "version": "1.0.0",
        "status": "running",
        "enterprises": list(active_enterprises.keys()),
        "active_tasks": list(active_tasks.keys())
    }

@app.get("/health")
async def health():
    """Проверка состояния сервиса"""
    try:
        # Проверяем подключение к БД
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        
        return {
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": f"error: {str(e)}",
            "timestamp": datetime.now()
        }

@app.get("/sync/status")
async def get_sync_status():
    """Получить статус синхронизации для всех предприятий"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT enterprise_id, asterisk_host, last_successful_sync, 
                   total_downloaded_events, failed_events_count, last_error_message
            FROM download_sync 
            ORDER BY enterprise_id
        """)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "enterprise_id": row[0],
                "asterisk_host": row[1],
                "last_sync": row[2],
                "total_downloaded": row[3],
                "failed_events": row[4],
                "last_error": row[5]
            })
        
        conn.close()
        return {
            "sync_status": results,
            "active_tasks": list(active_tasks.keys())
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения статуса: {str(e)}")

@app.post("/sync/{enterprise_id}")
async def sync_enterprise(enterprise_id: str, request: DownloadRequest, background_tasks: BackgroundTasks):
    """Запустить синхронизацию для предприятия"""
    active_enterprises = get_active_enterprises()
    if enterprise_id not in active_enterprises:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise_id} не найдено или не активно")
    
    if enterprise_id in active_tasks:
        raise HTTPException(status_code=409, detail=f"Синхронизация предприятия {enterprise_id} уже выполняется")
    
    # Запускаем синхронизацию в фоновом режиме
    background_tasks.add_task(
        sync_enterprise_data, 
        enterprise_id, 
        request.force_all, 
        request.date_from, 
        request.date_to
    )
    
    return {
        "message": f"Синхронизация предприятия {enterprise_id} запущена",
        "enterprise_id": enterprise_id,
        "force_all": request.force_all,
        "date_from": request.date_from,
        "date_to": request.date_to
    }

@app.post("/sync/all")
async def sync_all_enterprises(background_tasks: BackgroundTasks):
    """Запустить синхронизацию для всех предприятий"""
    started_tasks = []
    active_enterprises = get_active_enterprises()
    
    for enterprise_id in active_enterprises.keys():
        if enterprise_id not in active_tasks:
            background_tasks.add_task(sync_enterprise_data, enterprise_id)
            started_tasks.append(enterprise_id)
    
    return {
        "message": "Синхронизация запущена для предприятий",
        "started_tasks": started_tasks,
        "skipped_active": [eid for eid in active_enterprises.keys() if eid in active_tasks]
    }

@app.get("/enterprises")
async def get_enterprises():
    """Получить список активных предприятий"""
    active_enterprises = get_active_enterprises()
    enterprises = []
    for eid, config in active_enterprises.items():
        enterprises.append({
            "enterprise_id": eid,
            "name": config["name"],
            "host": config["host"],
            "ip": config["ip"],
            "token": config["token"][:10] + "..." if len(config["token"]) > 10 else config["token"]  # Скрываем полный токен
        })
    
    return {"enterprises": enterprises}

@app.post("/sync/live/all")
async def sync_live_all_enterprises(background_tasks: BackgroundTasks):
    """Синхронизация live событий для всех предприятий"""
    active_enterprises = get_active_enterprises()
    background_tasks.add_task(sync_live_events)
    return {
        "message": "Запущена синхронизация live событий для всех предприятий",
        "enterprises": list(active_enterprises.keys()),
        "type": "live_events",
        "target_table": "AlternativeAPIlogs"
    }

@app.post("/sync/live/{enterprise_id}")
async def sync_live_enterprise(enterprise_id: str, background_tasks: BackgroundTasks):
    """Синхронизация live событий для конкретного предприятия"""
    active_enterprises = get_active_enterprises()
    if enterprise_id not in active_enterprises:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise_id} не найдено или не активно")
    
    background_tasks.add_task(sync_live_events, enterprise_id)
    return {
        "message": f"Запущена синхронизация live событий для предприятия {enterprise_id}",
        "enterprise_id": enterprise_id,
        "type": "live_events",
        "target_table": "AlternativeAPIlogs"
    }

@app.get("/sync/live/status")
async def get_live_sync_status():
    """Получить статистику live событий"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Общая статистика по data_source
        cursor.execute("""
            SELECT data_source, COUNT(*) as count, 
                   MIN(start_time) as first_call,
                   MAX(start_time) as last_call
            FROM calls 
            GROUP BY data_source
            ORDER BY data_source
        """)
        
        data_sources = []
        for row in cursor.fetchall():
            data_sources.append({
                "data_source": row[0],
                "total_calls": row[1],
                "first_call": row[2],
                "last_call": row[3]
            })
        
        # Статистика по предприятиям и источникам
        cursor.execute("""
            SELECT enterprise_id, data_source, COUNT(*) as count
            FROM calls 
            GROUP BY enterprise_id, data_source
            ORDER BY enterprise_id, data_source
        """)
        
        enterprise_stats = []
        for row in cursor.fetchall():
            enterprise_stats.append({
                "enterprise_id": row[0],
                "data_source": row[1],
                "count": row[2]
            })
        
        conn.close()
        return {
            "data_sources": data_sources,
            "enterprise_breakdown": enterprise_stats,
            "auto_sync_interval": AUTO_SYNC_INTERVAL
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения статуса: {str(e)}")

@app.get("/sync/live/today")
async def get_live_events_today():
    """Получить количество неуспешных событий (восстановленных из AlternativeAPIlogs) за текущий день по предприятиям"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем неуспешные события (восстановленные из AlternativeAPIlogs) за сегодня по предприятиям
        cursor.execute("""
            SELECT enterprise_id, COUNT(*) as count
            FROM calls 
            WHERE data_source = 'recovery' 
              AND DATE(start_time) = CURRENT_DATE
            GROUP BY enterprise_id
            ORDER BY enterprise_id
        """)
        
        today_stats = {}
        total_today = 0
        
        for row in cursor.fetchall():
            enterprise_id = row[0]
            count = row[1]
            today_stats[enterprise_id] = count
            total_today += count
        
        # Получаем список всех активных предприятий для полноты картины
        active_enterprises = get_active_enterprises()
        for enterprise_id in active_enterprises.keys():
            if enterprise_id not in today_stats:
                today_stats[enterprise_id] = 0
        
        conn.close()
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_unsuccessful_events_today": total_today,
            "by_enterprise": today_stats
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения статистики: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007) 