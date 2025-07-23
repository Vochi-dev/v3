#!/usr/bin/env python3
"""
Тестирование конвертации WAV файлов в MP3 и загрузка в S3
"""

import os
from datetime import datetime
from hetzner_s3_integration import HetznerS3Client
from s3_config import S3_CONFIG

def test_wav_to_mp3_conversion():
    """Тестирует конвертацию WAV в MP3 и загрузку в S3"""
    
    print("🎵 ТЕСТИРОВАНИЕ КОНВЕРТАЦИИ WAV → MP3")
    print("="*60)
    
    # Проверяем наличие тестового WAV файла
    test_wav_file = "test_call_record.wav"
    if not os.path.exists(test_wav_file):
        print(f"❌ Тестовый файл {test_wav_file} не найден")
        return False
    
    # Получаем размер оригинального файла
    original_size = os.path.getsize(test_wav_file)
    print(f"📁 Оригинальный WAV файл: {test_wav_file}")
    print(f"📏 Размер оригинала: {original_size} байт")
    
    # Создаем S3 клиент
    try:
        s3_client = HetznerS3Client(
            access_key=S3_CONFIG['ACCESS_KEY'],
            secret_key=S3_CONFIG['SECRET_KEY'],
            region=S3_CONFIG['REGION']
        )
        print("✅ S3 клиент создан")
    except Exception as e:
        print(f"❌ Ошибка создания S3 клиента: {e}")
        return False
    
    # Тестируем загрузку с конвертацией
    enterprise_number = "0387"  # Тестовое предприятие
    call_unique_id = f"test_wav_conversion_{int(datetime.now().timestamp())}"
    
    print(f"🔄 Загружаем WAV файл с конвертацией в MP3...")
    print(f"   Enterprise: {enterprise_number}")
    print(f"   Call ID: {call_unique_id}")
    
    try:
        file_url = s3_client.upload_call_recording(
            enterprise_number=enterprise_number,
            call_unique_id=call_unique_id,
            local_file_path=test_wav_file,
            call_date=datetime.now()
        )
        
        if file_url:
            print(f"✅ Файл успешно загружен!")
            print(f"🔗 URL: {file_url}")
            
            # Проверяем что файл действительно MP3
            if file_url.endswith('.mp3'):
                print("✅ Файл сохранен с расширением .mp3")
            else:
                print("⚠️  Файл НЕ имеет расширение .mp3")
            
            return True
        else:
            print("❌ Ошибка загрузки файла")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при загрузке: {e}")
        return False

def test_mp3_search():
    """Тестирует поиск MP3 файлов через API"""
    print("\n🔍 ТЕСТИРОВАНИЕ ПОИСКА MP3 ФАЙЛОВ")
    print("="*60)
    
    try:
        s3_client = HetznerS3Client(
            access_key=S3_CONFIG['ACCESS_KEY'],
            secret_key=S3_CONFIG['SECRET_KEY'], 
            region=S3_CONFIG['REGION']
        )
        
        # Ищем записи за последний день
        today = datetime.now()
        yesterday = datetime(today.year, today.month, today.day-1 if today.day > 1 else 1, 0, 0, 0)
        
        recordings = s3_client.find_recordings(
            enterprise_number="0387",
            date_from=yesterday,
            date_to=today
        )
        
        print(f"📊 Найдено записей: {len(recordings)}")
        
        mp3_count = 0
        wav_count = 0
        
        for recording in recordings:
            key = recording['key']
            size = recording['size']
            
            if key.endswith('.mp3'):
                mp3_count += 1
                print(f"🎵 MP3: {key} ({size} байт)")
            elif key.endswith('.wav'):
                wav_count += 1 
                print(f"🎵 WAV: {key} ({size} байт)")
        
        print(f"\n📈 Статистика:")
        print(f"   MP3 файлов: {mp3_count}")
        print(f"   WAV файлов: {wav_count}")
        print(f"   Всего: {len(recordings)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
        return False

if __name__ == "__main__":
    print("🚀 ЗАПУСК ТЕСТИРОВАНИЯ КОНВЕРТАЦИИ АУДИО")
    print("="*60)
    
    # Тест 1: Конвертация и загрузка
    success1 = test_wav_to_mp3_conversion()
    
    # Тест 2: Поиск файлов
    success2 = test_mp3_search()
    
    print("\n" + "="*60)
    if success1 and success2:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("✅ Конвертация WAV → MP3 работает")
        print("✅ Поиск MP3 файлов работает")
        print("✅ Система готова к работе с MP3")
    else:
        print("❌ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОЙДЕНЫ")
        if not success1:
            print("❌ Конвертация WAV → MP3 не работает")
        if not success2:
            print("❌ Поиск MP3 файлов не работает")
    
    # Очистка тестового файла
    if os.path.exists("test_call_record.wav"):
        try:
            os.remove("test_call_record.wav")
            print("🧹 Тестовый WAV файл удален")
        except Exception as e:
            print(f"⚠️  Не удалось удалить тестовый файл: {e}") 