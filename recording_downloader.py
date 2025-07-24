"""
Модуль для загрузки записей разговоров
Общая логика для ночной джобы и точечной загрузки
"""

import os
import logging
import asyncio
import subprocess
import time
import json
from datetime import datetime
from typing import Dict, Optional, Tuple
import asyncpg

# Импорт S3 клиента
from hetzner_s3_integration import HetznerS3Client
from s3_config import S3_CONFIG

# Настройки SSH для Asterisk
ASTERISK_USER = "root"
ASTERISK_PASSWORD = "5atx9Ate@pbx"
ASTERISK_PORT = 5059

logger = logging.getLogger(__name__)

class RecordingDownloader:
    """Класс для скачивания и загрузки записей разговоров"""
    
    def __init__(self):
        self.s3_client = HetznerS3Client(
            access_key=S3_CONFIG['ACCESS_KEY'],
            secret_key=S3_CONFIG['SECRET_KEY'],
            region=S3_CONFIG['REGION']
        )
    
    async def download_single_recording(self, unique_id: str) -> Dict:
        """
        Скачивает одну запись по unique_id
        
        Args:
            unique_id: Уникальный ID звонка
            
        Returns:
            Dict с результатом загрузки
        """
        result = {
            'success': False,
            'unique_id': unique_id,
            's3_object_key': None,
            'call_url': None,
            'recording_duration': 0,
            'error_message': None
        }
        
        try:
            # 1. Получаем информацию о звонке из БД
            call_info = await self._get_call_info(unique_id)
            if not call_info:
                result['error_message'] = f"Звонок {unique_id} не найден в БД"
                return result
            
            # 2. Получаем информацию о предприятии
            enterprise_info = await self._get_enterprise_info(call_info['enterprise_id'])
            if not enterprise_info:
                result['error_message'] = f"Предприятие {call_info['enterprise_id']} не найдено"
                return result
            
            # 3. Создаем временную папку
            temp_dir = f"/tmp/single_download_{unique_id}_{int(time.time())}"
            os.makedirs(temp_dir, exist_ok=True)
            
            try:
                # 4. Скачиваем файл с Asterisk
                wav_file = await self._download_from_asterisk(unique_id, enterprise_info, temp_dir)
                if not wav_file:
                    result['error_message'] = f"Не удалось скачать файл {unique_id} с Asterisk"
                    return result
                
                # 5. Конвертируем в MP3
                mp3_file = await self._convert_to_mp3(wav_file)
                if not mp3_file:
                    result['error_message'] = f"Не удалось конвертировать файл {unique_id} в MP3"
                    return result
                
                # 6. Загружаем в S3 с существующим UUID токеном
                s3_result = await self._upload_to_s3_with_existing_uuid(
                    mp3_file, 
                    enterprise_info['name2'],  # name2 для папки S3
                    enterprise_info['number'],  # номер для метаданных 
                    unique_id,
                    call_info['uuid_token'],
                    os.path.basename(mp3_file),  # оригинальное имя файла
                    call_info['start_time']  # дата звонка
                )
                
                if s3_result:
                    s3_object_key, recording_duration = s3_result
                    
                    # 7. Обновляем БД
                    await self._update_call_record(unique_id, s3_object_key, recording_duration)
                    
                    result.update({
                        'success': True,
                        's3_object_key': s3_object_key,
                        'call_url': call_info['call_url'],
                        'recording_duration': recording_duration
                    })
                else:
                    result['error_message'] = f"Не удалось загрузить файл {unique_id} в S3"
                    
            finally:
                # Очистка временных файлов
                if os.path.exists(temp_dir):
                    subprocess.run(['rm', '-rf', temp_dir], capture_output=True)
                    
        except Exception as e:
            logger.error(f"Ошибка при загрузке записи {unique_id}: {e}")
            result['error_message'] = str(e)
            
        return result
    
    async def _get_call_info(self, unique_id: str) -> Optional[Dict]:
        """Получает информацию о звонке из БД"""
        try:
            conn = await asyncpg.connect(
                user='postgres',
                password='r/Yskqh/ZbZuvjb2b3ahfg==',
                database='postgres',
                host='localhost'
            )
            
            query = """
                SELECT unique_id, enterprise_id, uuid_token, call_url, s3_object_key, start_time
                FROM calls 
                WHERE unique_id = $1
            """
            
            result = await conn.fetchrow(query, unique_id)
            await conn.close()
            
            if result:
                return dict(result)
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения информации о звонке {unique_id}: {e}")
            return None
    
    async def _get_enterprise_info(self, enterprise_id: str) -> Optional[Dict]:
        """Получает информацию о предприятии"""
        try:
            conn = await asyncpg.connect(
                user='postgres',
                password='r/Yskqh/ZbZuvjb2b3ahfg==',
                database='postgres',
                host='localhost'
            )
            
            query = """
                SELECT number, name, name2, ip 
                FROM enterprises 
                WHERE number = $1
            """
            
            result = await conn.fetchrow(query, enterprise_id)
            await conn.close()
            
            if result:
                return dict(result)
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения информации о предприятии {enterprise_id}: {e}")
            return None
    
    async def _download_from_asterisk(self, unique_id: str, enterprise: Dict, temp_dir: str) -> Optional[str]:
        """Скачивает файл с Asterisk сервера"""
        try:
            wav_filename = f"{unique_id}.wav"
            local_wav_file = os.path.join(temp_dir, wav_filename)
            
            # Команда rsync для скачивания
            cmd = [
                'sshpass', '-p', ASTERISK_PASSWORD,
                'rsync', '-avz',
                '-e', f'ssh -p {ASTERISK_PORT} -o StrictHostKeyChecking=no',
                f'{ASTERISK_USER}@{enterprise["ip"]}:/var/spool/asterisk/monitor/{wav_filename}',
                f'{temp_dir}/'
            ]
            
            logger.info(f"Скачиваем {wav_filename} с {enterprise['ip']}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0 and os.path.exists(local_wav_file):
                logger.info(f"✅ Файл {wav_filename} скачан успешно")
                return local_wav_file
            else:
                logger.error(f"❌ Ошибка скачивания {wav_filename}: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при скачивании с Asterisk: {e}")
            return None
    
    async def _convert_to_mp3(self, wav_file: str) -> Optional[str]:
        """Конвертирует WAV в MP3"""
        try:
            mp3_file = wav_file.replace('.wav', '.mp3')
            
            cmd = [
                'ffmpeg', '-i', wav_file,
                '-acodec', 'mp3', '-ab', '128k',
                '-y', mp3_file
            ]
            
            logger.info(f"Конвертируем {wav_file} в MP3")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0 and os.path.exists(mp3_file):
                logger.info(f"✅ Конвертация завершена: {mp3_file}")
                return mp3_file
            else:
                logger.error(f"❌ Ошибка конвертации: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при конвертации: {e}")
            return None
    
    async def _upload_to_s3_with_existing_uuid(self, 
                                               mp3_file: str, 
                                               name2: str,
                                               enterprise_number: str, 
                                               unique_id: str,
                                               existing_uuid: str,
                                               original_filename: str,
                                               call_date) -> Optional[Tuple[str, int]]:
        """Загружает файл в S3 с существующим UUID токеном"""
        try:
            # name2 уже передан как параметр
            if not name2:
                logger.error(f"name2 не передан для звонка {unique_id}")
                return None
            
            # Формируем путь в S3 с оригинальным именем файла
            # Используем дату звонка (если None, то текущую)
            if call_date is None:
                call_date = datetime.now()
            object_key = f"CallRecords/{name2}/{call_date.year}/{call_date.month:02d}/{original_filename}"
            
            # Получаем длительность
            recording_duration = self.s3_client.get_audio_duration(mp3_file)
            if recording_duration is None:
                recording_duration = 0
            
            # Загружаем в S3
            self.s3_client.s3_client.upload_file(
                mp3_file,
                self.s3_client.bucket_name,
                object_key,
                ExtraArgs={
                    'Metadata': {
                        'enterprise-number': enterprise_number,
                        'call-unique-id': unique_id,
                        'upload-timestamp': datetime.utcnow().isoformat(),
                        'uuid-token': existing_uuid
                    },
                    'ContentType': 'audio/mpeg'
                }
            )
            
            logger.info(f"✅ Файл загружен в S3: {object_key}")
            return object_key, recording_duration
            
        except Exception as e:
            logger.error(f"Ошибка загрузки в S3: {e}")
            return None
    
    async def _update_call_record(self, unique_id: str, s3_object_key: str, recording_duration: int):
        """Обновляет запись в БД"""
        try:
            conn = await asyncpg.connect(
                user='postgres',
                password='r/Yskqh/ZbZuvjb2b3ahfg==',
                database='postgres',
                host='localhost'
            )
            
            query = """
                UPDATE calls 
                SET s3_object_key = $1, recording_duration = $2
                WHERE unique_id = $3
            """
            
            await conn.execute(query, s3_object_key, recording_duration, unique_id)
            await conn.close()
            
            logger.info(f"✅ БД обновлена для {unique_id}: {s3_object_key}")
            
        except Exception as e:
            logger.error(f"Ошибка обновления БД для {unique_id}: {e}") 