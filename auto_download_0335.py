#!/usr/bin/env python3
"""
АВТОМАТИЧЕСКАЯ МАССОВАЯ ЗАГРУЗКА ЗАПИСЕЙ ПРЕДПРИЯТИЯ 0335 (MERIDA)
Джоба запускается 23 июля 2025 в 15:30 GMT+3 (12:30 UTC)

Сервер: 10.88.10.34:5059
Предприятие: 0335 (Merida)
name2: 375291111121
"""

import asyncio
import os
import sys
import time
import tempfile
import shutil
from datetime import datetime
from typing import List, Tuple
import logging
import subprocess

# Добавляем путь для импорта
sys.path.append('app')

from hetzner_s3_integration import HetznerS3Client
from s3_config import S3_CONFIG
from app.services.postgres import init_pool, update_call_recording_info

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'/tmp/auto_download_0335_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Константы для предприятия 0335
ENTERPRISE_NUMBER = "0335"
ENTERPRISE_NAME = "Merida"
ENTERPRISE_NAME2 = "375291111121"
ASTERISK_SERVER = "10.88.10.34"
ASTERISK_PORT = "5059"
ASTERISK_USER = "root"
ASTERISK_PASSWORD = "5atx9Ate@pbx"
TEMP_DIR = "/tmp/asterisk_0335_auto_download"

def print_banner():
    """Печатает красивый баннер начала работы"""
    banner = f"""
{'='*70}
🤖 АВТОМАТИЧЕСКАЯ ДЖОБА ЗАГРУЗКИ ЗАПИСЕЙ ПРЕДПРИЯТИЯ 0335
{'='*70}
📅 Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🏢 Предприятие: {ENTERPRISE_NUMBER} ({ENTERPRISE_NAME})
🌐 Сервер: {ASTERISK_SERVER}:{ASTERISK_PORT}
📁 name2: {ENTERPRISE_NAME2}
🎯 Структура S3: CallRecords/{ENTERPRISE_NAME2}/год/месяц/
{'='*70}
"""
    print(banner)
    logger.info(f"СТАРТ АВТОМАТИЧЕСКОЙ ЗАГРУЗКИ ПРЕДПРИЯТИЯ {ENTERPRISE_NUMBER}")

def check_server_connection() -> bool:
    """Проверяет подключение к серверу Asterisk"""
    try:
        logger.info(f"🔍 Проверяем подключение к серверу {ASTERISK_SERVER}:{ASTERISK_PORT}...")
        
        cmd = [
            'sshpass', '-p', ASTERISK_PASSWORD,
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            '-p', ASTERISK_PORT, f'{ASTERISK_USER}@{ASTERISK_SERVER}',
            'echo "Подключение успешно"'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            logger.info("✅ Подключение к серверу Asterisk успешно")
            return True
        else:
            logger.error(f"❌ Ошибка подключения к серверу: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Исключение при подключении к серверу: {e}")
        return False

def analyze_recordings() -> Tuple[int, str]:
    """Анализирует записи на сервере"""
    try:
        logger.info("📊 Анализируем записи на сервере...")
        
        cmd = [
            'sshpass', '-p', ASTERISK_PASSWORD,
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            '-p', ASTERISK_PORT, f'{ASTERISK_USER}@{ASTERISK_SERVER}',
            'ls -la /var/spool/asterisk/monitor/*.wav | wc -l && du -sh /var/spool/asterisk/monitor/'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            file_count = int(lines[0])
            size_info = lines[1] if len(lines) > 1 else "Неизвестно"
            
            logger.info(f"📁 Найдено файлов: {file_count}")
            logger.info(f"📦 Общий размер: {size_info}")
            
            return file_count, size_info
        else:
            logger.error(f"❌ Ошибка анализа записей: {result.stderr}")
            return 0, "Ошибка"
            
    except Exception as e:
        logger.error(f"❌ Исключение при анализе записей: {e}")
        return 0, "Исключение"

def download_recordings(file_count: int) -> bool:
    """Скачивает все записи с сервера"""
    try:
        logger.info(f"📥 Начинаем скачивание {file_count} файлов...")
        
        # Создаем временную папку
        os.makedirs(TEMP_DIR, exist_ok=True)
        logger.info(f"📁 Временная папка: {TEMP_DIR}")
        
        # Команда rsync для скачивания
        cmd = [
            'sshpass', '-p', ASTERISK_PASSWORD,
            'rsync', '-avz', '--progress',
            '-e', f'ssh -p {ASTERISK_PORT} -o StrictHostKeyChecking=no',
            f'{ASTERISK_USER}@{ASTERISK_SERVER}:/var/spool/asterisk/monitor/*.wav',
            f'{TEMP_DIR}/'
        ]
        
        start_time = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 минут таймаут
        end_time = time.time()
        
        download_time = int(end_time - start_time)
        
        if result.returncode == 0:
            # Проверяем количество скачанных файлов
            downloaded_files = len([f for f in os.listdir(TEMP_DIR) if f.endswith('.wav')])
            logger.info(f"✅ Скачивание завершено за {download_time} сек")
            logger.info(f"📁 Скачано файлов: {downloaded_files}")
            return True
        else:
            logger.error(f"❌ Ошибка скачивания: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Исключение при скачивании: {e}")
        return False

async def process_and_upload_recordings() -> Tuple[int, int]:
    """Обрабатывает и загружает записи в S3"""
    try:
        logger.info("🔄 Начинаем обработку и загрузку в S3...")
        
        # Инициализация S3 клиента
        s3_client = HetznerS3Client(
            access_key=S3_CONFIG['ACCESS_KEY'],
            secret_key=S3_CONFIG['SECRET_KEY'],
            region=S3_CONFIG['REGION']
        )
        
        # Инициализация БД
        await init_pool()
        logger.info("✅ Подключение к БД инициализировано")
        
        # Получаем список WAV файлов
        wav_files = [f for f in os.listdir(TEMP_DIR) if f.endswith('.wav')]
        total_files = len(wav_files)
        
        logger.info(f"🎵 Найдено WAV файлов для обработки: {total_files}")
        
        success_count = 0
        error_count = 0
        
        for i, wav_file in enumerate(wav_files, 1):
            try:
                # Извлекаем call_unique_id из имени файла
                call_unique_id = wav_file.replace('.wav', '')
                wav_path = os.path.join(TEMP_DIR, wav_file)
                
                logger.info(f"📤 [{i}/{total_files}] Обрабатываем: {call_unique_id}")
                
                # Загружаем в S3 с конвертацией
                upload_result = s3_client.upload_call_recording(
                    enterprise_number=ENTERPRISE_NUMBER,
                    call_unique_id=call_unique_id,
                    local_file_path=wav_path
                )
                
                if upload_result:
                    file_url, object_key, uuid_token, recording_duration = upload_result
                    
                    # Формируем безопасную публичную ссылку
                    public_call_url = f"/recordings/file/{uuid_token}"
                    
                    # Сохраняем в БД
                    db_success = await update_call_recording_info(
                        call_unique_id=call_unique_id,
                        call_url=public_call_url,
                        s3_object_key=object_key,
                        uuid_token=uuid_token,
                        recording_duration=recording_duration
                    )
                    
                    if db_success:
                        success_count += 1
                        logger.info(f"✅ [{i}/{total_files}] Успешно: {call_unique_id} (UUID: {uuid_token})")
                    else:
                        error_count += 1
                        logger.error(f"❌ [{i}/{total_files}] Ошибка БД: {call_unique_id}")
                else:
                    error_count += 1
                    logger.error(f"❌ [{i}/{total_files}] Ошибка загрузки: {call_unique_id}")
                    
                # Прогресс каждые 10 файлов
                if i % 10 == 0:
                    logger.info(f"📊 Прогресс: {i}/{total_files} ({i/total_files*100:.1f}%)")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"❌ [{i}/{total_files}] Исключение при обработке {wav_file}: {e}")
        
        return success_count, error_count
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при обработке: {e}")
        return 0, 0

def cleanup_temp_files():
    """Очищает временные файлы"""
    try:
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            logger.info(f"🧹 Временная папка {TEMP_DIR} удалена")
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке временных файлов: {e}")

def print_final_report(start_time: float, file_count: int, success_count: int, error_count: int):
    """Печатает финальный отчет"""
    end_time = time.time()
    total_time = int(end_time - start_time)
    
    report = f"""
{'='*70}
🎉 АВТОМАТИЧЕСКАЯ ЗАГРУЗКА ЗАВЕРШЕНА
{'='*70}
📅 Время завершения: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏱️  Общее время: {total_time} сек ({total_time//60} мин {total_time%60} сек)
📁 Всего файлов: {file_count}
✅ Успешно загружено: {success_count}
❌ Ошибок: {error_count}
📈 Успешность: {success_count/max(file_count,1)*100:.1f}%
🏢 Предприятие: {ENTERPRISE_NUMBER} ({ENTERPRISE_NAME})
🗂️  Структура S3: CallRecords/{ENTERPRISE_NAME2}/2025/07/
{'='*70}
"""
    print(report)
    logger.info(f"ЗАВЕРШЕНА АВТОМАТИЧЕСКАЯ ЗАГРУЗКА: {success_count}/{file_count} файлов за {total_time} сек")

async def main():
    """Основная функция автоматической загрузки"""
    start_time = time.time()
    
    try:
        print_banner()
        
        # 1. Проверка подключения к серверу
        if not check_server_connection():
            logger.error("❌ Не удалось подключиться к серверу Asterisk")
            return
        
        # 2. Анализ записей
        file_count, size_info = analyze_recordings()
        if file_count == 0:
            logger.warning("⚠️ На сервере не найдено записей для загрузки")
            return
        
        # 3. Скачивание записей
        if not download_recordings(file_count):
            logger.error("❌ Ошибка при скачивании записей")
            return
        
        # 4. Обработка и загрузка в S3
        success_count, error_count = await process_and_upload_recordings()
        
        # 5. Финальный отчет
        print_final_report(start_time, file_count, success_count, error_count)
        
        # 6. Очистка
        cleanup_temp_files()
        
        logger.info("🎯 АВТОМАТИЧЕСКАЯ ДЖОБА ЗАВЕРШЕНА УСПЕШНО")
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в main(): {e}")
        cleanup_temp_files()

if __name__ == "__main__":
    asyncio.run(main()) 