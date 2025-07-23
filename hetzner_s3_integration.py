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
    
    def upload_call_recording(self, 
                            enterprise_number: str,
                            call_unique_id: str, 
                            local_file_path: str,
                            call_date: Optional[datetime] = None) -> Optional[str]:
        """
        Загружает запись разговора в Object Storage
        
        Args:
            enterprise_number: Номер предприятия (например, "0387")
            call_unique_id: Уникальный ID звонка
            local_file_path: Путь к локальному файлу
            call_date: Дата звонка (по умолчанию - текущая)
            
        Returns:
            URL загруженного файла или None при ошибке
        """
        if call_date is None:
            call_date = datetime.now()
            
        # Получаем name2 предприятия из БД
        enterprise_name2 = self._get_enterprise_name2(enterprise_number)
        
        # Формируем структуру папок: CallRecords/name2/год/месяц/
        date_path = call_date.strftime('%Y/%m')
        file_extension = os.path.splitext(local_file_path)[1]
        
        # Ключ объекта (новая структура путей)
        object_key = f"CallRecords/{enterprise_name2}/{date_path}/{call_unique_id}{file_extension}"
        
        try:
            # Загружаем файл
            self.s3_client.upload_file(
                local_file_path, 
                self.bucket_name, 
                object_key,
                ExtraArgs={
                    'Metadata': {
                        'enterprise-number': enterprise_number,
                        'call-unique-id': call_unique_id,
                        'upload-timestamp': datetime.utcnow().isoformat(),
                        'call-date': call_date.isoformat()
                    },
                    'ContentType': 'audio/wav'  # или определять автоматически
                }
            )
            
            # Возвращаем URL файла
            file_url = f"https://{self.bucket_name}.{self.region}.your-objectstorage.com/{object_key}"
            self.logger.info(f"Файл загружен: {file_url}")
            return file_url
            
        except Exception as e:
            self.logger.error(f"Ошибка загрузки файла {local_file_path}: {e}")
            return None
    
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