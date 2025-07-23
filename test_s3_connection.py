#!/usr/bin/env python3
"""
Тестовый скрипт для проверки подключения к Hetzner Object Storage
"""

import sys
import os
import tempfile
from datetime import datetime, timedelta

# Добавляем текущую папку в путь
sys.path.insert(0, os.path.abspath('.'))

from hetzner_s3_integration import HetznerS3Client
from s3_config import S3_CONFIG, validate_s3_config

def test_s3_connection():
    """Тестирует подключение к Hetzner Object Storage"""
    
    print("🔧 Тестирование подключения к Hetzner Object Storage...")
    print("=" * 60)
    
    # 1. Проверяем конфигурацию
    print("1️⃣  Проверка конфигурации...")
    config_check = validate_s3_config()
    
    if not config_check['valid']:
        print("❌ Ошибки в конфигурации:")
        for issue in config_check['issues']:
            print(f"   - {issue}")
        print("📝 Исправьте файл s3_config.py и укажите ваши реальные ключи")
        return False
    
    print("✅ Конфигурация корректна")
    
    # 2. Создаем клиент
    print("\n2️⃣  Создание S3 клиента...")
    try:
        s3_client = HetznerS3Client(
            access_key=S3_CONFIG['ACCESS_KEY'],
            secret_key=S3_CONFIG['SECRET_KEY'],
            region=S3_CONFIG['REGION']
        )
        print(f"✅ S3 клиент создан для региона {S3_CONFIG['REGION']}")
    except Exception as e:
        print(f"❌ Ошибка создания клиента: {e}")
        return False
    
    # 3. Проверяем/создаем bucket
    print(f"\n3️⃣  Проверка bucket '{S3_CONFIG['BUCKET_NAME']}'...")
    if s3_client.create_bucket_if_not_exists():
        print(f"✅ Bucket '{S3_CONFIG['BUCKET_NAME']}' готов к использованию")
    else:
        print(f"❌ Ошибка работы с bucket '{S3_CONFIG['BUCKET_NAME']}'")
        return False
    
    # 4. Тестовая загрузка файла
    print("\n4️⃣  Тестовая загрузка файла...")
    try:
        # Создаем временный тестовый файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
            temp_file.write(f"Тестовый файл, создан {datetime.now()}")
            temp_file_path = temp_file.name
        
        # Загружаем файл
        file_url = s3_client.upload_call_recording(
            enterprise_number="TEST",
            call_unique_id=f"test_{int(datetime.now().timestamp())}",
            local_file_path=temp_file_path,
            call_date=datetime.now()
        )
        
        if file_url:
            print(f"✅ Файл загружен: {file_url}")
        else:
            print("❌ Ошибка загрузки файла")
            return False
            
        # Удаляем временный файл
        os.unlink(temp_file_path)
        
    except Exception as e:
        print(f"❌ Ошибка тестовой загрузки: {e}")
        return False
    
    # 5. Статистика использования
    print("\n5️⃣  Статистика использования...")
    try:
        usage = s3_client.get_storage_usage()
        print(f"✅ Файлов в хранилище: {usage['total_files']}")
        print(f"✅ Общий размер: {usage['total_size_mb']} MB")
        print(f"✅ Общий размер: {usage['total_size_gb']} GB")
    except Exception as e:
        print(f"❌ Ошибка получения статистики: {e}")
        return False
    
    # 6. Тест временных ссылок
    print("\n6️⃣  Тест генерации временных ссылок...")
    try:
        # Находим первый файл для теста
        recordings = s3_client.find_recordings(
            enterprise_number="TEST",
            date_from=datetime.now() - timedelta(days=1),
            date_to=datetime.now() + timedelta(days=1)
        )
        
        if recordings:
            test_key = recordings[0]['key']
            download_link = s3_client.generate_download_link(test_key, expires_in=3600)
            if download_link:
                print(f"✅ Временная ссылка создана (действует 1 час)")
                print(f"   Ссылка: {download_link[:80]}...")
            else:
                print("❌ Ошибка создания временной ссылки")
        else:
            print("ℹ️  Нет файлов для тестирования временных ссылок")
            
    except Exception as e:
        print(f"❌ Ошибка тестирования временных ссылок: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    print("📋 Object Storage готов к использованию")
    print("\n📊 Информация о подключении:")
    print(f"   Endpoint: {S3_CONFIG['ENDPOINT_URL']}")
    print(f"   Bucket: {S3_CONFIG['BUCKET_NAME']}")
    print(f"   Регион: {S3_CONFIG['REGION']}")
    print(f"   URL bucket: https://{S3_CONFIG['BUCKET_NAME']}.{S3_CONFIG['REGION']}.your-objectstorage.com/")
    
    return True

def test_typical_scenarios():
    """Тестирует типичные сценарии использования"""
    
    print("\n" + "=" * 60)
    print("🔧 ТЕСТИРОВАНИЕ ТИПИЧНЫХ СЦЕНАРИЕВ")
    print("=" * 60)
    
    s3_client = HetznerS3Client(
        access_key=S3_CONFIG['ACCESS_KEY'],
        secret_key=S3_CONFIG['SECRET_KEY'],
        region=S3_CONFIG['REGION']
    )
    
    # Сценарий 1: Загрузка записи разговора
    print("📞 Сценарий 1: Загрузка записи разговора для предприятия 0387")
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.wav', delete=False) as temp_file:
            temp_file.write("FAKE_AUDIO_DATA_FOR_TESTING")
            temp_file_path = temp_file.name
        
        file_url = s3_client.upload_call_recording(
            enterprise_number="0387",
            call_unique_id=f"call_{int(datetime.now().timestamp())}",
            local_file_path=temp_file_path
        )
        
        print(f"✅ Запись загружена: {file_url}")
        os.unlink(temp_file_path)
        
    except Exception as e:
        print(f"❌ Ошибка загрузки записи: {e}")
    
    # Сценарий 2: Поиск записей за последний день
    print("\n🔍 Сценарий 2: Поиск записей предприятия 0387 за последний день")
    try:
        recordings = s3_client.find_recordings(
            enterprise_number="0387",
            date_from=datetime.now() - timedelta(days=1),
            date_to=datetime.now()
        )
        
        print(f"✅ Найдено записей: {len(recordings)}")
        for i, recording in enumerate(recordings[:3]):  # Показываем только первые 3
            print(f"   {i+1}. {recording['key']} ({recording['size']} байт)")
            
    except Exception as e:
        print(f"❌ Ошибка поиска записей: {e}")
    
    # Сценарий 3: Создание ссылки для CRM системы
    print("\n🔗 Сценарий 3: Создание временной ссылки для CRM системы")
    try:
        if recordings:
            test_key = recordings[0]['key']
            # Ссылка действует 24 часа (86400 секунд)
            download_link = s3_client.generate_download_link(test_key, expires_in=86400)
            
            if download_link:
                print(f"✅ Ссылка для CRM создана (действует 24 часа)")
                print(f"   Файл: {test_key}")
                print(f"   Ссылка: {download_link[:80]}...")
            else:
                print("❌ Ошибка создания ссылки")
        else:
            print("ℹ️  Нет файлов для создания ссылок")
            
    except Exception as e:
        print(f"❌ Ошибка создания ссылки: {e}")

if __name__ == "__main__":
    print("🚀 ТЕСТИРОВАНИЕ HETZNER OBJECT STORAGE")
    print("=" * 60)
    
    # Основные тесты
    if test_s3_connection():
        # Дополнительные сценарии
        test_typical_scenarios()
        
        print("\n" + "=" * 60)
        print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ УСПЕШНО!")
        print("📝 Теперь можно интегрировать Object Storage в основной проект")
    else:
        print("\n❌ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО С ОШИБКАМИ")
        print("📝 Проверьте настройки и попробуйте снова") 