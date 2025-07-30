#!/usr/bin/env python3
"""
ЕЖЕДНЕВНАЯ АВТОМАТИЧЕСКАЯ ЗАГРУЗКА ЗАПИСЕЙ
Запускается каждый день в 21:00 GMT+3 (18:00 UTC)

Скачивает записи разговоров со всех предприятий где parameter_option_3 = true
Использует правильные name2 для структуры папок S3: CallRecords/{name2}/год/месяц/
"""

import asyncio
import os
import sys
import time
import tempfile
import shutil
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import logging
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Добавляем путь для импорта
sys.path.append('app')

from hetzner_s3_integration import HetznerS3Client
from s3_config import S3_CONFIG
from app.services.postgres import init_pool, update_call_recording_info

# Настройка логирования
log_filename = f'/tmp/daily_recordings_sync_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Константы для подключения к Asterisk серверам
ASTERISK_PORT = "5059"
ASTERISK_USER = "root"
ASTERISK_PASSWORD = "5atx9Ate@pbx"
TEMP_BASE_DIR = "/tmp/daily_recordings_sync"

# 🚀 Настройки параллельного скачивания
ENABLE_PARALLEL_DOWNLOAD = True  # ✅ ВКЛЮЧЕНО: исправлена логика rsync
MAX_PARALLEL_THREADS = 4         # Количество потоков (рекомендуется 4)

# 🎯 Настройки параллельной обработки предприятий 
MAX_PARALLEL_ENTERPRISES = 5     # Количество предприятий обрабатываемых одновременно

class DailyRecordingsSync:
    def __init__(self):
        self.s3_client = None
        self.enterprises = []
        self.total_found_on_servers = 0    # Всего найдено файлов на серверах
        self.total_needed_from_db = 0      # Всего нужно скачать из БД  
        self.total_downloaded = 0          # Реально скачано файлов
        self.total_success = 0             # Успешно загружено на S3
        self.total_errors = 0              # Ошибки загрузки на S3
        self.total_skipped = 0             # Пропущено (уже загружено)
        
    def print_banner(self):
        """Печатает красивый баннер начала работы"""
        banner = f"""
{'='*80}
🚀 ЕЖЕДНЕВНАЯ АВТОМАТИЧЕСКАЯ ЗАГРУЗКА ЗАПИСЕЙ РАЗГОВОРОВ
{'='*80}
📅 Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⚙️  Версия: Оптимизированная с ПАРАЛЛЕЛЬНОЙ обработкой
🎯 Загрузка с предприятий где parameter_option_3 = true
📁 Структура S3: CallRecords/{{name2}}/год/месяц/
🔗 Безопасные ссылки: /recordings/file/{{uuid_token}}
🛡️ Защита от дублирования: проверка s3_object_key перед загрузкой

🚀 ПАРАЛЛЕЛЬНАЯ ОБРАБОТКА:
   ⚡ Предприятий одновременно: {MAX_PARALLEL_ENTERPRISES}
   🔄 Потоков скачивания на предприятие: {MAX_PARALLEL_THREADS}
   💾 Параллельных загрузок на S3: 10 одновременно
   ⏱️ Таймаут скачивания файла: 60 сек (было 300)
   📈 Ожидаемое ускорение: до 10x

📊 Лог файл: {log_filename}
{'='*80}
"""
        print(banner)
        logger.info("🚀 СТАРТ ЕЖЕДНЕВНОЙ АВТОМАТИЧЕСКОЙ ЗАГРУЗКИ С ПАРАЛЛЕЛЬНОЙ ОБРАБОТКОЙ")
    
    async def get_enterprises_list(self) -> List[Dict]:
        """Получает список предприятий с parameter_option_3 = true"""
        try:
            import asyncpg
            conn = await asyncpg.connect(
                host="localhost",
                port=5432,
                user="postgres", 
                password="r/Yskqh/ZbZuvjb2b3ahfg==",
                database="postgres"
            )
            
            logger.info("🔍 Получаем список предприятий с parameter_option_3 = true...")
            
            rows = await conn.fetch("""
                SELECT id, number, name, name2, ip, host, parameter_option_3 
                FROM enterprises 
                WHERE parameter_option_3 = true 
                  AND active = true
                ORDER BY number
            """)
            
            enterprises = []
            for row in rows:
                enterprise = {
                    'id': row['id'],
                    'number': row['number'],
                    'name': row['name'],
                    'name2': row['name2'],
                    'ip': row['ip'],
                    'host': row['host']
                }
                enterprises.append(enterprise)
                logger.info(f"✅ {enterprise['number']} ({enterprise['name']}) → name2: {enterprise['name2']} → IP: {enterprise['ip']}")
            
            await conn.close()
            logger.info(f"📊 Найдено предприятий для обработки: {len(enterprises)}")
            return enterprises
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения списка предприятий: {e}")
            return []
    
    def check_server_connection(self, enterprise: Dict) -> bool:
        """Проверяет подключение к серверу Asterisk"""
        try:
            logger.info(f"🔍 Проверяем подключение к серверу {enterprise['name']} ({enterprise['ip']}:{ASTERISK_PORT})...")
            
            cmd = [
                'sshpass', '-p', ASTERISK_PASSWORD,
                'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
                '-p', ASTERISK_PORT, f'{ASTERISK_USER}@{enterprise["ip"]}',
                'echo "Подключение успешно"'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0:
                logger.info(f"✅ Подключение к серверу {enterprise['name']} успешно")
                return True
            else:
                logger.error(f"❌ Ошибка подключения к серверу {enterprise['name']}: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Исключение при подключении к серверу {enterprise['name']}: {e}")
            return False
    
    async def get_calls_needing_download(self, enterprise: Dict) -> List[str]:
        """
        Получает список unique_id звонков которые нужно скачать:
        - s3_object_key = null (еще не загружены на S3)
        - call_url не пустой (есть запись)
        """
        try:
            import asyncpg
            conn = await asyncpg.connect(
                host="localhost",
                port=5432,
                user="postgres", 
                password="r/Yskqh/ZbZuvjb2b3ahfg==",
                database="postgres"
            )
            
            logger.info(f"🔍 {enterprise['name']}: ищем записи для скачивания в БД...")
            
            # Ищем записи для этого предприятия где есть call_url но нет s3_object_key
            # ИСПРАВЛЕНО: убран лимит 1000 - обрабатываем ВСЕ нужные записи
            rows = await conn.fetch("""
                SELECT unique_id, call_url 
                FROM calls 
                WHERE enterprise_id = $1 
                  AND call_url IS NOT NULL 
                  AND s3_object_key IS NULL
                ORDER BY start_time DESC
            """, enterprise['number'])
            
            unique_ids = [row['unique_id'] for row in rows]
            
            await conn.close()
            
            logger.info(f"📊 {enterprise['name']}: найдено записей для скачивания: {len(unique_ids)}")
            
            if unique_ids:
                logger.info(f"📝 {enterprise['name']}: примеры unique_id: {unique_ids[:3]}")
            
            return unique_ids
            
        except Exception as e:
            logger.error(f"❌ {enterprise['name']}: ошибка поиска записей в БД: {e}")
            return []

    def analyze_recordings(self, enterprise: Dict) -> Tuple[int, str]:
        """Анализирует записи на сервере и фильтрует файлы больше 2 КБ"""
        try:
            logger.info(f"📊 Анализируем записи на сервере {enterprise['name']}...")
            
            # Команда для получения файлов больше 2 КБ (2048 байт)
            cmd = [
                'sshpass', '-p', ASTERISK_PASSWORD,
                'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
                '-p', ASTERISK_PORT, f'{ASTERISK_USER}@{enterprise["ip"]}',
                'find /var/spool/asterisk/monitor/ -name "*.wav" -size +2k 2>/dev/null | wc -l && du -sh /var/spool/asterisk/monitor/ 2>/dev/null || echo "0 0K"'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                try:
                    file_count = int(lines[0])
                except:
                    file_count = 0
                size_info = lines[1] if len(lines) > 1 else "Неизвестно"
                
                logger.info(f"📁 {enterprise['name']}: найдено файлов >2KB: {file_count}")
                logger.info(f"📦 {enterprise['name']}: общий размер: {size_info}")
                
                return file_count, size_info
            else:
                logger.error(f"❌ Ошибка анализа записей {enterprise['name']}: {result.stderr}")
                return 0, "Ошибка"
                
        except Exception as e:
            logger.error(f"❌ Исключение при анализе записей {enterprise['name']}: {e}")
            return 0, "Исключение"
    
    def download_recordings_parallel(self, enterprise: Dict, temp_dir: str, needed_unique_ids: List[str]) -> bool:
        """🚀 БЫСТРОЕ ПАРАЛЛЕЛЬНОЕ СКАЧИВАНИЕ - в 3-4 раза быстрее"""
        
        # 1. Получаем список файлов на сервере больше 2 КБ
        logger.info(f"🔍 {enterprise['name']}: получаем список файлов >2KB для параллельного скачивания...")
        
        cmd_list = [
            'sshpass', '-p', ASTERISK_PASSWORD,
            'ssh', '-p', ASTERISK_PORT, '-o', 'StrictHostKeyChecking=no',
            f'{ASTERISK_USER}@{enterprise["ip"]}',
            'find /var/spool/asterisk/monitor/ -name "*.wav" -size +2k -printf "%f\\n" 2>/dev/null'
        ]
        
        try:
            result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return False
                
            server_files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip().endswith('.wav')]
            
            if not server_files:
                logger.info(f"⚠️ {enterprise['name']}: нет файлов >2KB для скачивания")
                return True
            
            # 2. Фильтруем файлы по списку needed_unique_ids из БД
            needed_files = []
            for unique_id in needed_unique_ids:
                wav_file = f"{unique_id}.wav"
                if wav_file in server_files:
                    needed_files.append(wav_file)
            
            if not needed_files:
                logger.info(f"⚠️ {enterprise['name']}: нет файлов из БД для скачивания (найдено на сервере: {len(server_files)}, нужно из БД: {len(needed_unique_ids)})")
                return True
                
            total_files = len(needed_files)
            logger.info(f"📁 {enterprise['name']}: найдено файлов для скачивания: {total_files} (из {len(server_files)} на сервере)")
            
        except Exception as e:
            logger.error(f"❌ {enterprise['name']}: ошибка получения списка файлов: {e}")
            return False
        
        # 3. Разделяем нужные файлы на группы для параллельной загрузки
        files_per_thread = max(1, total_files // MAX_PARALLEL_THREADS)
        file_groups = []
        
        for i in range(0, total_files, files_per_thread):
            group = needed_files[i:i + files_per_thread]
            file_groups.append(group)
        
        actual_threads = min(len(file_groups), MAX_PARALLEL_THREADS)
        logger.info(f"🚀 {enterprise['name']}: запускаем {actual_threads} потоков скачивания")
        
        # 4. Функция скачивания для одного потока
        def download_group(group_files: List[str], thread_id: int) -> Dict:
            group_size = len(group_files)
            
            thread_dir = os.path.join(temp_dir, f"thread_{thread_id}")
            os.makedirs(thread_dir, exist_ok=True)
            
            success_count = 0
            start_time = time.time()
            
            # 🔥 ИСПРАВЛЕНО: Скачиваем каждый файл отдельно в цикле
            for file_name in group_files:
                try:
                    cmd_rsync = [
                        'sshpass', '-p', ASTERISK_PASSWORD,
                        'rsync', '-avz', 
                        '-e', f'ssh -p {ASTERISK_PORT} -o StrictHostKeyChecking=no',
                        f'{ASTERISK_USER}@{enterprise["ip"]}:/var/spool/asterisk/monitor/{file_name}',
                        f'{thread_dir}/'
                    ]
                    
                    result = subprocess.run(cmd_rsync, capture_output=True, text=True, timeout=60)  # 1 мин на файл
                    
                    if result.returncode == 0:
                        # Проверяем что файл скачался
                        local_file = os.path.join(thread_dir, file_name)
                        if os.path.exists(local_file):
                            # Перемещаем в основную папку
                            dst = os.path.join(temp_dir, file_name)
                            shutil.move(local_file, dst)
                            success_count += 1
                    else:
                        logger.error(f"❌ {enterprise['name']} [Поток {thread_id}]: ошибка файла {file_name}: {result.stderr}")
                        
                except Exception as e:
                    logger.error(f"❌ {enterprise['name']} [Поток {thread_id}]: исключение файла {file_name}: {e}")
            
            download_time = int(time.time() - start_time)
            logger.info(f"✅ {enterprise['name']} [Поток {thread_id}]: скачано {success_count}/{group_size} файлов за {download_time} сек")
            
            # Удаляем папку потока
            shutil.rmtree(thread_dir, ignore_errors=True)
            
            return {'success_count': success_count}
        
        # 5. Запускаем параллельное скачивание
        total_start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=actual_threads) as executor:
            future_to_thread = {
                executor.submit(download_group, group, i+1): i+1 
                for i, group in enumerate(file_groups)
            }
            
            total_downloaded = 0
            for future in as_completed(future_to_thread):
                try:
                    result = future.result()
                    total_downloaded += result['success_count']
                except Exception as e:
                    logger.error(f"❌ {enterprise['name']}: исключение в потоке: {e}")
        
        # 6. Проверяем результат
        total_time = int(time.time() - total_start_time)
        final_files = len([f for f in os.listdir(temp_dir) if f.endswith('.wav')])
        
        logger.info(f"✅ {enterprise['name']}: параллельное скачивание завершено за {total_time} сек")
        logger.info(f"📊 {enterprise['name']}: файлов скачано: {final_files}/{total_files}")
        
        # Обновляем общую статистику скачанных файлов
        self.total_downloaded += final_files
        
        if total_time > 0:
            speed = (final_files / total_time) * 60
            logger.info(f"🚀 {enterprise['name']}: скорость: {speed:.1f} файлов/мин (ускорение в ~3x)")
        
        return final_files > 0

    async def download_recordings(self, enterprise: Dict, needed_unique_ids: List[str]) -> bool:
        """Скачивает только нужные записи с сервера предприятия"""
        if not needed_unique_ids:
            logger.info(f"⚠️ {enterprise['name']}: нет записей для скачивания")
            return True
            
        try:
            temp_dir = os.path.join(TEMP_BASE_DIR, enterprise['number'])
            logger.info(f"📥 {enterprise['name']}: начинаем скачивание {len(needed_unique_ids)} файлов...")
            
            # Создаем временную папку
            os.makedirs(temp_dir, exist_ok=True)
            logger.info(f"📁 {enterprise['name']}: временная папка: {temp_dir}")
            
            # 🚀 ПРОВЕРЯЕМ: использовать ли параллельное скачивание?
            if ENABLE_PARALLEL_DOWNLOAD and len(needed_unique_ids) >= 50:  # Для больших предприятий
                logger.info(f"🚀 {enterprise['name']}: используем параллельное скачивание ({MAX_PARALLEL_THREADS} потоков)")
                return self.download_recordings_parallel(enterprise, temp_dir, needed_unique_ids)
            
            # Обычное последовательное скачивание (для небольших предприятий)
            logger.info(f"📥 {enterprise['name']}: используем обычное скачивание")
            
            success_count = 0
            start_time = time.time()
            
            # Скачиваем каждый нужный файл отдельно
            for i, unique_id in enumerate(needed_unique_ids, 1):
                try:
                    wav_file = f"{unique_id}.wav"
                    
                    # Проверяем что файл существует и больше 2KB на сервере
                    check_cmd = [
                        'sshpass', '-p', ASTERISK_PASSWORD,
                        'ssh', '-p', ASTERISK_PORT, '-o', 'StrictHostKeyChecking=no',
                        f'{ASTERISK_USER}@{enterprise["ip"]}',
                        f'find /var/spool/asterisk/monitor/ -name "{wav_file}" -size +2k 2>/dev/null'
                    ]
                    
                    check_result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
                    if check_result.returncode != 0 or not check_result.stdout.strip():
                        logger.warning(f"⚠️ {enterprise['name']} [{i}/{len(needed_unique_ids)}]: файл {wav_file} не найден или <2KB")
                        continue
                    
                    # Скачиваем файл
                    cmd = [
                        'sshpass', '-p', ASTERISK_PASSWORD,
                        'rsync', '-avz',
                        '-e', f'ssh -p {ASTERISK_PORT} -o StrictHostKeyChecking=no',
                        f'{ASTERISK_USER}@{enterprise["ip"]}:/var/spool/asterisk/monitor/{wav_file}',
                        f'{temp_dir}/'
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)  # 1 мин на файл
                    
                    if result.returncode == 0:
                        local_file = os.path.join(temp_dir, wav_file)
                        if os.path.exists(local_file):
                            success_count += 1
                            if i % 10 == 0:
                                logger.info(f"📥 {enterprise['name']}: скачано {success_count}/{i} файлов")
                    else:
                        logger.error(f"❌ {enterprise['name']} [{i}/{len(needed_unique_ids)}]: ошибка скачивания {wav_file}")
                        
                except Exception as e:
                    logger.error(f"❌ {enterprise['name']} [{i}/{len(needed_unique_ids)}]: исключение при скачивании {unique_id}: {e}")
            
            end_time = time.time()
            download_time = int(end_time - start_time)
            
            logger.info(f"✅ {enterprise['name']}: скачивание завершено за {download_time} сек")
            logger.info(f"📁 {enterprise['name']}: скачано файлов: {success_count}/{len(needed_unique_ids)}")
            
            # Обновляем общую статистику скачанных файлов
            self.total_downloaded += success_count
            
            return success_count > 0
                
        except Exception as e:
            logger.error(f"❌ {enterprise['name']}: исключение при скачивании: {e}")
            return False
    
    async def process_and_upload_recordings(self, enterprise: Dict) -> Tuple[int, int]:
        """Обрабатывает и загружает записи в S3 для одного предприятия"""
        temp_dir = os.path.join(TEMP_BASE_DIR, enterprise['number'])
        
        if not os.path.exists(temp_dir):
            logger.warning(f"⚠️ {enterprise['name']}: временная папка не найдена")
            return 0, 0
            
        try:
            logger.info(f"🔄 {enterprise['name']}: начинаем обработку и загрузку в S3...")
            
            # Получаем список WAV файлов
            wav_files = [f for f in os.listdir(temp_dir) if f.endswith('.wav')]
            total_files = len(wav_files)
            
            if total_files == 0:
                logger.info(f"⚠️ {enterprise['name']}: нет WAV файлов для обработки")
                return 0, 0
                
            logger.info(f"🎵 {enterprise['name']}: найдено WAV файлов для обработки: {total_files}")
            
            # 🚀 ПАРАЛЛЕЛЬНАЯ загрузка на S3 - исправлена основная проблема!
            logger.info(f"🚀 {enterprise['name']}: начинаем ПАРАЛЛЕЛЬНУЮ загрузку {total_files} файлов на S3...")
            
            # Создаем задачи для параллельной обработки всех файлов
            async def process_single_file(wav_file: str, file_index: int) -> Tuple[bool, str]:
                """Обрабатывает один файл параллельно"""
                try:
                    call_unique_id = wav_file.replace('.wav', '')
                    wav_path = os.path.join(temp_dir, wav_file)
                    
                    logger.info(f"📤 {enterprise['name']} [{file_index}/{total_files}] Обрабатываем: {call_unique_id}")
                    
                    # 🛡️ ЗАЩИТА ОТ ДУБЛИРОВАНИЯ: проверяем что файл еще не загружен
                    from app.services.postgres import get_call_recording_info
                    call_info = await get_call_recording_info(call_unique_id)
                    if call_info and call_info.get('s3_object_key'):
                        logger.info(f"⚠️ {enterprise['name']} [{file_index}/{total_files}] Пропускаем {call_unique_id} - уже загружен на S3")
                        return False, call_unique_id  # Не ошибка, но и не успех
                    
                    # Создаем правильный объект для S3 клиента с name2
                    upload_result = await self.upload_recording_with_name2(
                        enterprise_number=enterprise['number'],
                        enterprise_name2=enterprise['name2'],
                        call_unique_id=call_unique_id,
                        local_file_path=wav_path
                    )
                    
                    if upload_result:
                        file_url, object_key, uuid_token, recording_duration = upload_result
                        
                        # ИСПРАВЛЕНО: НЕ меняем call_url и uuid_token! Сохраняем только S3 данные
                        db_success = await update_call_recording_info(
                            call_unique_id=call_unique_id,
                            s3_object_key=object_key,
                            recording_duration=recording_duration
                        )
                        
                        if db_success:
                            logger.info(f"✅ {enterprise['name']} [{file_index}/{total_files}] Успешно: {call_unique_id} (UUID: {uuid_token})")
                            return True, call_unique_id
                        else:
                            logger.error(f"❌ {enterprise['name']} [{file_index}/{total_files}] Ошибка БД: {call_unique_id}")
                            return False, call_unique_id
                    else:
                        logger.error(f"❌ {enterprise['name']} [{file_index}/{total_files}] Ошибка загрузки: {call_unique_id}")
                        return False, call_unique_id
                        
                except Exception as e:
                    logger.error(f"❌ {enterprise['name']} [{file_index}/{total_files}] Исключение при обработке {wav_file}: {e}")
                    return False, wav_file.replace('.wav', '')
            
            # Создаем семафор для ограничения одновременных загрузок (чтобы не перегрузить S3)
            upload_semaphore = asyncio.Semaphore(10)  # Максимум 10 одновременных загрузок
            
            async def process_with_semaphore(wav_file: str, file_index: int) -> Tuple[bool, str]:
                async with upload_semaphore:
                    return await process_single_file(wav_file, file_index)
            
            # Запускаем ВСЕ файлы параллельно!
            tasks = [
                process_with_semaphore(wav_file, i+1) 
                for i, wav_file in enumerate(wav_files)
            ]
            
            # Ждем завершения всех загрузок
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Подсчитываем результаты
            success_count = 0
            error_count = 0
            skipped_count = 0
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"❌ {enterprise['name']}: исключение при обработке файла {i+1}: {result}")
                    error_count += 1
                else:
                    success, call_id = result
                    if success:
                        success_count += 1
                    elif call_id:  # False но с call_id означает "пропущен"
                        skipped_count += 1
                    else:
                        error_count += 1
                
                # Прогресс каждые 50 файлов для параллельной обработки
                if (i + 1) % 50 == 0:
                    logger.info(f"📊 {enterprise['name']}: обработано {i+1}/{total_files} файлов ({(i+1)/total_files*100:.1f}%)")
            
            logger.info(f"📊 {enterprise['name']}: успешно {success_count}, ошибок {error_count}, пропущено {skipped_count}")
            
            # Обновляем глобальную статистику пропущенных
            self.total_skipped += skipped_count
            
            return success_count, error_count
            
        except Exception as e:
            logger.error(f"❌ {enterprise['name']}: критическая ошибка при обработке: {e}")
            return 0, 0
    
    async def upload_recording_with_name2(self, enterprise_number: str, enterprise_name2: str, 
                                         call_unique_id: str, local_file_path: str) -> Optional[Tuple[str, str, str, int]]:
        """Загружает запись с правильным использованием name2"""
        try:
            # Конвертируем WAV в MP3
            mp3_file = await self.convert_wav_to_mp3(local_file_path)
            if not mp3_file:
                logger.error(f"Ошибка конвертации файла: {local_file_path}")
                return None
            
            # Получаем длительность записи
            recording_duration = self.s3_client.get_audio_duration(mp3_file)
            if recording_duration is None:
                recording_duration = 0
            
            # ИСПРАВЛЕНО: Получаем существующий UUID из call_url вместо генерации нового!
            from app.services.postgres import get_call_recording_info
            call_info = await get_call_recording_info(call_unique_id)
            
            if not call_info or not call_info.get('call_url'):
                logger.error(f"❌ Не найден call_url для звонка {call_unique_id}")
                return None
                
            # Извлекаем UUID из существующей ссылки
            call_url = call_info['call_url']
            if '/recordings/file/' in call_url:
                uuid_token = call_url.split('/recordings/file/')[-1]
                logger.info(f"✅ Используем существующий UUID: {uuid_token} из {call_url}")
            else:
                logger.error(f"❌ Неверный формат call_url: {call_url}")
                return None
            
            # Создаем правильный путь с name2
            call_date = datetime.now()
            object_key = f"CallRecords/{enterprise_name2}/{call_date.year:04d}/{call_date.month:02d}/{call_unique_id}.mp3"
            
            logger.info(f"📁 Правильный путь S3: {object_key}")
            logger.info(f"🔑 UUID токен: {uuid_token}")
            
            # Загружаем файл в S3 с правильным boto3 методом
            try:
                self.s3_client.s3_client.upload_file(
                    mp3_file, 
                    self.s3_client.bucket_name, 
                    object_key,
                    ExtraArgs={
                        'Metadata': {
                            'enterprise-number': enterprise_number,
                            'call-unique-id': call_unique_id,
                            'upload-timestamp': datetime.utcnow().isoformat(),
                            'uuid-token': uuid_token
                        },
                        'ContentType': 'audio/mpeg'
                    }
                )
                
                # Удаляем временный MP3 файл
                if os.path.exists(mp3_file):
                    os.remove(mp3_file)
                    
                file_url = f"https://{self.s3_client.bucket_name}.{self.s3_client.region}.your-objectstorage.com/{object_key}"
                logger.info(f"✅ Файл загружен: {file_url} (длительность: {recording_duration}с)")
                return file_url, object_key, uuid_token, recording_duration
                
            except Exception as upload_error:
                logger.error(f"❌ Ошибка загрузки файла в S3: {upload_error}")
                # Удаляем временный MP3 файл даже при ошибке
                if os.path.exists(mp3_file):
                    os.remove(mp3_file)
                return None
                
        except Exception as e:
            logger.error(f"❌ Исключение при загрузке записи: {e}")
            return None
    
    async def convert_wav_to_mp3(self, wav_file_path: str) -> Optional[str]:
        """Конвертирует WAV файл в MP3"""
        try:
            # Создаем временный файл для MP3
            mp3_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            mp3_file.close()
            
            # Конвертируем с помощью ffmpeg
            cmd = ['ffmpeg', '-y', '-i', wav_file_path, '-acodec', 'mp3', '-ab', '64k', mp3_file.name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0 and os.path.exists(mp3_file.name):
                # Проверяем размеры файлов
                wav_size = os.path.getsize(wav_file_path)
                mp3_size = os.path.getsize(mp3_file.name)
                compression = (1 - mp3_size / wav_size) * 100
                
                logger.info(f"🔄 Конвертация WAV→MP3: {wav_size} → {mp3_size} байт (сжатие {compression:.1f}%)")
                return mp3_file.name
            else:
                logger.error(f"❌ Ошибка конвертации ffmpeg: {result.stderr}")
                if os.path.exists(mp3_file.name):
                    os.remove(mp3_file.name)
                return None
                
        except Exception as e:
            logger.error(f"❌ Исключение при конвертации: {e}")
            return None
    
    def cleanup_temp_files(self):
        """Очищает временные файлы"""
        try:
            if os.path.exists(TEMP_BASE_DIR):
                shutil.rmtree(TEMP_BASE_DIR)
                logger.info(f"🧹 Временная папка {TEMP_BASE_DIR} удалена")
        except Exception as e:
            logger.error(f"❌ Ошибка при очистке временных файлов: {e}")
    
    def print_final_report(self, start_time: float):
        """Печатает финальный отчет с правильной статистикой"""
        end_time = time.time()
        total_time = int(end_time - start_time)
        
        # ИСПРАВЛЕНО: правильная статистика
        download_success_rate = (self.total_downloaded / max(self.total_needed_from_db, 1)) * 100
        upload_success_rate = (self.total_success / max(self.total_downloaded, 1)) * 100
        overall_success_rate = (self.total_success / max(self.total_needed_from_db, 1)) * 100
        
        report = f"""
{'='*80}
🎉 ЕЖЕДНЕВНАЯ АВТОМАТИЧЕСКАЯ ЗАГРУЗКА ЗАВЕРШЕНА
{'='*80}
📅 Время завершения: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏱️  Общее время: {total_time} сек ({total_time//60} мин {total_time%60} сек)
🏢 Предприятий обработано: {len(self.enterprises)}

📊 ДЕТАЛЬНАЯ СТАТИСТИКА:
  📁 Найдено на серверах (>2KB): {self.total_found_on_servers}
  🎯 Нужно скачать из БД: {self.total_needed_from_db}
  📥 Реально скачано: {self.total_downloaded}
  ✅ Загружено на S3: {self.total_success}
  ⚠️ Пропущено (уже загружено): {self.total_skipped}
  ❌ Ошибок загрузки: {self.total_errors}

📈 ПОКАЗАТЕЛИ ЭФФЕКТИВНОСТИ:
  🔄 Скачивание: {download_success_rate:.1f}% ({self.total_downloaded}/{self.total_needed_from_db})
  ☁️  Загрузка S3: {upload_success_rate:.1f}% ({self.total_success}/{self.total_downloaded})
  🎯 Общая успешность: {overall_success_rate:.1f}% ({self.total_success}/{self.total_needed_from_db})

📊 Лог файл: {log_filename}
{'='*80}
"""
        print(report)
        logger.info(f"ЗАВЕРШЕНА ЕЖЕДНЕВНАЯ ЗАГРУЗКА: {self.total_success}/{self.total_needed_from_db} файлов (общий успех {overall_success_rate:.1f}%) за {total_time} сек")
    
    async def process_single_enterprise(self, enterprise: Dict, semaphore: asyncio.Semaphore) -> Tuple[int, int, int, int]:
        """Обрабатывает одно предприятие (для параллельного выполнения)"""
        async with semaphore:  # Ограничиваем количество одновременных соединений
            logger.info(f"\n{'='*60}")
            logger.info(f"🏢 НАЧИНАЕМ ОБРАБОТКУ: {enterprise['number']} ({enterprise['name']})")
            logger.info(f"📁 name2: {enterprise['name2']} → IP: {enterprise['ip']}")
            logger.info(f"{'='*60}")
            
            try:
                # Проверка подключения
                if not self.check_server_connection(enterprise):
                    logger.error(f"❌ {enterprise['name']}: пропускаем из-за ошибки подключения")
                    return 0, 0, 0, 0  # server_files, needed_files, success, errors
                
                # Получаем список записей из БД, которые нужно скачать
                needed_unique_ids = await self.get_calls_needing_download(enterprise)
                if not needed_unique_ids:
                    logger.info(f"⚠️ {enterprise['name']}: нет записей для скачивания из БД")
                    return 0, 0, 0, 0
                
                # Анализ записей на сервере
                server_file_count, size_info = self.analyze_recordings(enterprise)
                logger.info(f"📊 {enterprise['name']}: на сервере {server_file_count} файлов >2KB, в БД нужно скачать {len(needed_unique_ids)}")
                
                # Скачивание только нужных записей
                if not await self.download_recordings(enterprise, needed_unique_ids):
                    logger.error(f"❌ {enterprise['name']}: пропускаем из-за ошибки скачивания")
                    return server_file_count, len(needed_unique_ids), 0, 0
                
                # Обработка и загрузка в S3
                success_count, error_count = await self.process_and_upload_recordings(enterprise)
                
                logger.info(f"✅ {enterprise['name']}: завершено ({success_count}/{len(needed_unique_ids)} успешно)")
                
                return server_file_count, len(needed_unique_ids), success_count, error_count
                
            except Exception as e:
                logger.error(f"❌ {enterprise['name']}: критическая ошибка: {e}")
                return 0, 0, 0, 0

    async def run(self):
        """Основная функция ежедневной загрузки с параллельной обработкой предприятий"""
        start_time = time.time()
        
        try:
            self.print_banner()
            
            # 1. Инициализация S3 клиента
            try:
                self.s3_client = HetznerS3Client(
                    access_key=S3_CONFIG['ACCESS_KEY'],
                    secret_key=S3_CONFIG['SECRET_KEY'],
                    region=S3_CONFIG['REGION']
                )
                logger.info("✅ S3 клиент инициализирован")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации S3: {e}")
                return
            
            # 2. Инициализация БД
            try:
                await init_pool()
                logger.info("✅ Подключение к БД инициализировано")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации БД: {e}")
                return
            
            # 3. Получение списка предприятий
            self.enterprises = await self.get_enterprises_list()
            if not self.enterprises:
                logger.warning("⚠️ Не найдено предприятий для обработки")
                return
            
            # 4. Создание базовой временной папки
            os.makedirs(TEMP_BASE_DIR, exist_ok=True)
            
            # 5. 🚀 ПАРАЛЛЕЛЬНАЯ обработка предприятий
            logger.info(f"🚀 НАЧИНАЕМ ПАРАЛЛЕЛЬНУЮ ОБРАБОТКУ {len(self.enterprises)} ПРЕДПРИЯТИЙ")
            logger.info(f"⚡ Максимум одновременно: {MAX_PARALLEL_ENTERPRISES} предприятий")
            logger.info(f"{'='*80}")
            
            # Создаем семафор для ограничения количества одновременных соединений
            semaphore = asyncio.Semaphore(MAX_PARALLEL_ENTERPRISES)
            
            # Запускаем все предприятия параллельно
            tasks = [
                self.process_single_enterprise(enterprise, semaphore) 
                for enterprise in self.enterprises
            ]
            
            # Ждем завершения всех задач
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Собираем статистику
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"❌ Исключение при обработке предприятия {self.enterprises[i]['name']}: {result}")
                    continue
                    
                server_files, needed_files, success, errors = result
                self.total_found_on_servers += server_files
                self.total_needed_from_db += needed_files
                self.total_success += success
                self.total_errors += errors
            
            # 6. Финальный отчет
            self.print_final_report(start_time)
            
            # 7. Очистка
            self.cleanup_temp_files()
            
            logger.info("🎯 ЕЖЕДНЕВНАЯ АВТОМАТИЧЕСКАЯ ЗАГРУЗКА ЗАВЕРШЕНА УСПЕШНО")
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в main(): {e}")
            self.cleanup_temp_files()

async def main():
    """Точка входа"""
    sync = DailyRecordingsSync()
    await sync.run()

if __name__ == "__main__":
    asyncio.run(main()) 