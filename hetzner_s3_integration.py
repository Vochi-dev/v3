#!/usr/bin/env python3
"""
Модуль для работы с Hetzner Object Storage (S3-совместимый)
Для проекта записей телефонных разговоров
"""

import boto3
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import logging
import psycopg2
import tempfile
from pydub import AudioSegment
import uuid

class HetznerS3Client:
    """Клиент для работы с Hetzner Object Storage"""
    
    def __init__(self, access_key: str, secret_key: str, region: str = 'fsn1'):
        """
        Инициализация клиента
        
        Args:
            access_key: Ваш S3 access key
            secret_key: Ваш S3 secret key  
            region: Регион (fsn1, nbg1, hel1)
        """
        self.region = region
        self.endpoint_url = f'https://{region}.your-objectstorage.com'
        self.bucket_name = 'vochi'
        
        # Создаем S3 клиент
        self.s3_client = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        self.logger = logging.getLogger(__name__)
        
    def create_bucket_if_not_exists(self) -> bool:
        """Создает bucket если он не существует"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            self.logger.info(f"Bucket '{self.bucket_name}' уже существует")
            return True
        except:
            try:
                self.s3_client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': self.region}
                )
                self.logger.info(f"Bucket '{self.bucket_name}' успешно создан")
                return True
            except Exception as e:
                self.logger.error(f"Ошибка создания bucket: {e}")
                return False
    
    def _get_enterprise_name2(self, enterprise_number: str) -> str:
        """
        Получает name2 предприятия из базы данных
        
        Args:
            enterprise_number: Номер предприятия
            
        Returns:
            str: name2 предприятия или enterprise_number если не найден
        """
        try:
            conn = psycopg2.connect(
                host="localhost",
                database="postgres",
                user="postgres",
                password="r/Yskqh/ZbZuvjb2b3ahfg=="
            )
            
            with conn.cursor() as cursor:
                cursor.execute("SELECT name2 FROM enterprises WHERE number = %s", (enterprise_number,))
                result = cursor.fetchone()
                
                if result and result[0]:
                    return result[0]
                else:
                    logging.warning(f"Предприятие {enterprise_number} не найдено в БД или name2 пустой")
                    return enterprise_number
                    
        except Exception as e:
            logging.error(f"Ошибка получения name2 для предприятия {enterprise_number}: {e}")
            return enterprise_number
        finally:
            if 'conn' in locals():
                conn.close()
    
    def _convert_wav_to_mp3(self, wav_file_path: str, quality: str = "192k") -> str:
        """
        Конвертирует WAV файл в MP3
        
        Args:
            wav_file_path: Путь к WAV файлу
            quality: Качество MP3 (битрейт), например "128k", "192k", "320k"
            
        Returns:
            str: Путь к созданному MP3 файлу
        """
        try:
            # Загружаем WAV файл
            audio = AudioSegment.from_wav(wav_file_path)
            
            # Создаем временный MP3 файл
            temp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_mp3_path = temp_mp3.name
            temp_mp3.close()
            
            # Конвертируем в MP3 с указанным качеством
            audio.export(
                temp_mp3_path, 
                format="mp3",
                bitrate=quality,
                parameters=["-q:a", "2"]  # Хорошее качество кодирования
            )
            
            # Получаем размеры файлов для сравнения
            original_size = os.path.getsize(wav_file_path)
            converted_size = os.path.getsize(temp_mp3_path)
            compression_ratio = round((1 - converted_size / original_size) * 100, 1)
            
            logging.info(f"Конвертация WAV→MP3: {original_size} → {converted_size} байт (сжатие {compression_ratio}%)")
            
            return temp_mp3_path
            
        except Exception as e:
            logging.error(f"Ошибка конвертации {wav_file_path} в MP3: {e}")
            # Если конвертация не удалась, возвращаем оригинальный файл
            return wav_file_path
    
    def generate_file_path(self, enterprise_number: str, call_unique_id: str, call_date: datetime) -> tuple[str, str]:
        """
        Генерирует путь файла в старой структуре + UUID токен для безопасной ссылки
        
        Args:
            enterprise_number: Номер предприятия (например, "0387")
            call_unique_id: Уникальный ID звонка (например, "123456.0254")
            call_date: Дата звонка
            
        Returns:
            Кортеж (object_key, uuid_token) - путь в S3 и UUID токен
        """
        # Получаем name2 предприятия из БД
        enterprise_name2 = self._get_enterprise_name2(enterprise_number)
        
        # Формируем структуру папок как в старом проекте: CallRecords/name2/год/месяц/
        date_path = call_date.strftime('%Y/%m')
        
        # Оригинальное имя файла с расширением .mp3 (заменяем .wav если есть)
        original_filename = call_unique_id
        if original_filename.endswith('.wav'):
            original_filename = original_filename[:-4]  # убираем .wav
        filename = f"{original_filename}.mp3"
        
        # Путь в старой структуре
        object_key = f"CallRecords/{enterprise_name2}/{date_path}/{filename}"
        
        # UUID токен для безопасной ссылки
        uuid_token = str(uuid.uuid4())
        
        logging.info(f"Сгенерирован путь: {object_key}")
        logging.info(f"UUID токен: {uuid_token}")
        
        return object_key, uuid_token
    
    def _get_enterprise_name2(self, enterprise_number: str) -> str:
        """
        Получает name2 предприятия из БД или использует маппинг
        
        Args:
            enterprise_number: Номер предприятия (например, "0334")
            
        Returns:
            name2 предприятия для структуры папок
        """
        # Пока используем прямой маппинг для известных предприятий
        enterprise_mapping = {
            "0334": "375291111120",  # Megas
            "0387": "375291111177",  # Другое предприятие
            # Добавить другие предприятия по мере необходимости
        }
        
        name2 = enterprise_mapping.get(enterprise_number, enterprise_number)
        logging.info(f"Маппинг предприятия {enterprise_number} → {name2}")
        return name2
    
    def get_audio_duration(self, file_path: str) -> Optional[int]:
        """
        Получает длительность аудиофайла в секундах
        
        Args:
            file_path: Путь к аудиофайлу
            
        Returns:
            Длительность в секундах или None при ошибке
        """
        try:
            audio = AudioSegment.from_file(file_path)
            duration_seconds = int(len(audio) / 1000)  # Переводим из миллисекунд в секунды
            logging.info(f"Длительность файла {file_path}: {duration_seconds} секунд")
            return duration_seconds
        except Exception as e:
            logging.error(f"Ошибка получения длительности файла {file_path}: {e}")
            return None
    
    def upload_call_recording(self, 
                            enterprise_number: str,
                            call_unique_id: str, 
                            local_file_path: str,
                            call_date: Optional[datetime] = None) -> Optional[tuple[str, str, str, int]]:
        """
        Загружает запись разговора в Object Storage
        
        Args:
            enterprise_number: Номер предприятия (например, "0387")
            call_unique_id: Уникальный ID звонка (например, "123456.0254")
            local_file_path: Путь к локальному файлу
            call_date: Дата звонка (по умолчанию - текущая)
            
        Returns:
            Кортеж (file_url, object_key, uuid_token, recording_duration) или None при ошибке
        """
        if call_date is None:
            call_date = datetime.now()
            
        # Генерируем путь в старой структуре + UUID токен
        object_key, uuid_token = self.generate_file_path(enterprise_number, call_unique_id, call_date)
        
        # Определяем нужна ли конвертация
        file_extension = os.path.splitext(local_file_path)[1].lower()
        file_to_upload = local_file_path
        temp_files_to_cleanup = []
        
        # Если файл WAV, конвертируем в MP3
        if file_extension == '.wav':
            logging.info(f"Конвертируем WAV файл в MP3: {local_file_path}")
            mp3_file_path = self._convert_wav_to_mp3(local_file_path)
            
            if mp3_file_path != local_file_path:  # Конвертация успешна
                file_to_upload = mp3_file_path
                file_extension = '.mp3'
                temp_files_to_cleanup.append(mp3_file_path)
                logging.info(f"Конвертация завершена: {mp3_file_path}")
            else:
                logging.warning(f"Конвертация не удалась, загружаем оригинальный WAV файл")
        
        # Получаем длительность готового файла для загрузки
        recording_duration = self.get_audio_duration(file_to_upload)
        if recording_duration is None:
            logging.warning(f"Не удалось получить длительность файла: {file_to_upload}")
            recording_duration = 0  # Устанавливаем по умолчанию
        
        try:
            # Определяем Content-Type по расширению файла
            content_type = 'audio/mpeg' if file_extension == '.mp3' else 'audio/wav'
            
            # Загружаем файл (может быть конвертированный MP3)
            self.s3_client.upload_file(
                file_to_upload, 
                self.bucket_name, 
                object_key,
                ExtraArgs={
                    'Metadata': {
                        'enterprise-number': enterprise_number,
                        'call-unique-id': call_unique_id,
                        'upload-timestamp': datetime.utcnow().isoformat(),
                        'call-date': call_date.isoformat(),
                        'original-format': os.path.splitext(local_file_path)[1].lower(),
                        'converted-to-mp3': 'true' if file_to_upload != local_file_path else 'false'
                    },
                    'ContentType': content_type
                }
            )
            
            # Возвращаем URL файла, object_key, UUID токен и длительность записи
            file_url = f"https://{self.bucket_name}.{self.region}.your-objectstorage.com/{object_key}"
            logging.info(f"Файл загружен: {file_url} (длительность: {recording_duration}с)")
            return file_url, object_key, uuid_token, recording_duration
            
        except Exception as e:
            logging.error(f"Ошибка загрузки файла {file_to_upload}: {e}")
            return None
            
        finally:
            # Очищаем временные файлы
            for temp_file in temp_files_to_cleanup:
                try:
                    if os.path.exists(temp_file):
                        os.unlink(temp_file)
                        logging.debug(f"Удален временный файл: {temp_file}")
                except Exception as e:
                    logging.warning(f"Не удалось удалить временный файл {temp_file}: {e}")
    
    def generate_download_link(self, object_key: str, expires_in: int = 3600) -> Optional[str]:
        """
        Генерирует временную ссылку для скачивания
        
        Args:
            object_key: Ключ объекта в bucket
            expires_in: Время жизни ссылки в секундах (по умолчанию 1 час)
            
        Returns:
            Подписанная URL для скачивания
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': object_key},
                ExpiresIn=expires_in
            )
            return url
        except Exception as e:
            self.logger.error(f"Ошибка генерации ссылки для {object_key}: {e}")
            return None
    
    def find_recordings(self, 
                       enterprise_number: str, 
                       date_from: datetime, 
                       date_to: datetime) -> List[Dict]:
        """
        Находит записи разговоров по критериям
        
        Args:
            enterprise_number: Номер предприятия
            date_from: Начальная дата поиска
            date_to: Конечная дата поиска
            
        Returns:
            Список найденных записей
        """
        recordings = []
        
        # Получаем name2 предприятия из БД
        enterprise_name2 = self._get_enterprise_name2(enterprise_number)
        
        # Формируем список месяцев для поиска
        current_date = date_from.date()
        end_date = date_to.date()
        
        # Уникальные месяцы в диапазоне дат
        months_to_search = set()
        temp_date = current_date
        while temp_date <= end_date:
            months_to_search.add(temp_date.strftime('%Y/%m'))
            # Переходим к следующему месяцу
            if temp_date.month == 12:
                temp_date = temp_date.replace(year=temp_date.year + 1, month=1)
            else:
                temp_date = temp_date.replace(month=temp_date.month + 1)
        
        # Ищем записи в каждом месяце
        for month_prefix in months_to_search:
            prefix = f"CallRecords/{enterprise_name2}/{month_prefix}/"
            
            try:
                response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=prefix
                )
                
                if 'Contents' in response:
                    for obj in response['Contents']:
                        # Фильтруем по дате изменения файла
                        file_date = obj['LastModified'].date()
                        if date_from.date() <= file_date <= date_to.date():
                            
                            # Получаем метаданные
                            try:
                                metadata_response = self.s3_client.head_object(
                                    Bucket=self.bucket_name,
                                    Key=obj['Key']
                                )
                                metadata = metadata_response.get('Metadata', {})
                            except Exception as e:
                                logging.warning(f"Не удалось получить метаданные для {obj['Key']}: {e}")
                                metadata = {}
                            
                            recordings.append({
                                'key': obj['Key'],
                                'size': obj['Size'],
                                'last_modified': obj['LastModified'],
                                'metadata': metadata,
                                'download_url': f"https://{self.bucket_name}.{self.region}.your-objectstorage.com/{obj['Key']}"
                            })
                        
            except Exception as e:
                self.logger.error(f"Ошибка поиска записей для {month_prefix}: {e}")
            
        return recordings
    
    def delete_old_recordings(self, days_to_keep: int = 90) -> int:
        """
        Удаляет записи старше указанного количества дней
        
        Args:
            days_to_keep: Количество дней для хранения
            
        Returns:
            Количество удаленных файлов
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        deleted_count = 0
        
        try:
            # Получаем список всех объектов
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix='call-recordings/'
            )
            
            for page in pages:
                if 'Contents' not in page:
                    continue
                    
                for obj in page['Contents']:
                    if obj['LastModified'].replace(tzinfo=None) < cutoff_date:
                        self.s3_client.delete_object(
                            Bucket=self.bucket_name,
                            Key=obj['Key']
                        )
                        deleted_count += 1
                        self.logger.info(f"Удален старый файл: {obj['Key']}")
                        
        except Exception as e:
            self.logger.error(f"Ошибка удаления старых записей: {e}")
            
        return deleted_count
    
    def get_storage_usage(self) -> Dict:
        """
        Получает информацию об использовании хранилища
        
        Returns:
            Словарь с информацией об использовании
        """
        total_size = 0
        total_files = 0
        
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name)
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        total_size += obj['Size']
                        total_files += 1
                        
        except Exception as e:
            self.logger.error(f"Ошибка получения статистики: {e}")
            
        return {
            'total_files': total_files,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / 1024 / 1024, 2),
            'total_size_gb': round(total_size / 1024 / 1024 / 1024, 2)
        }


# Пример использования
if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(level=logging.INFO)
    
    # ВНИМАНИЕ: Замените на ваши реальные ключи!
    ACCESS_KEY = "YOUR_ACCESS_KEY_HERE"
    SECRET_KEY = "YOUR_SECRET_KEY_HERE"
    
    # Создаем клиент
    s3_client = HetznerS3Client(ACCESS_KEY, SECRET_KEY)
    
    # Создаем bucket если нужно
    s3_client.create_bucket_if_not_exists()
    
    # Пример загрузки файла
    # file_url = s3_client.upload_call_recording(
    #     enterprise_number="0387",
    #     call_unique_id="test_call_123456",
    #     local_file_path="/path/to/call_recording.wav"
    # )
    
    # Пример поиска записей
    # recordings = s3_client.find_recordings(
    #     enterprise_number="0387",
    #     date_from=datetime(2025, 7, 1),
    #     date_to=datetime(2025, 7, 23)
    # )
    
    # Статистика использования
    # usage = s3_client.get_storage_usage()
    # print(f"Всего файлов: {usage['total_files']}")
    # print(f"Размер: {usage['total_size_gb']} GB") 