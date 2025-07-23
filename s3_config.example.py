#!/usr/bin/env python3
"""
ПРИМЕР конфигурации для Hetzner Object Storage
СКОПИРУЙТЕ этот файл как s3_config.py и укажите ваши реальные ключи!
"""

import os
from typing import Dict

# Настройки Hetzner Object Storage
S3_CONFIG = {
    # ВНИМАНИЕ: Замените на ваши реальные ключи из Hetzner Console!
    # Security -> S3 Credentials -> Generate credentials
    'ACCESS_KEY': os.getenv('HETZNER_S3_ACCESS_KEY', 'ЗАМЕНИТЕ_НА_ВАШ_ACCESS_KEY'),
    'SECRET_KEY': os.getenv('HETZNER_S3_SECRET_KEY', 'ЗАМЕНИТЕ_НА_ВАШ_SECRET_KEY'),
    
    # Настройки endpoint (измените регион при необходимости)
    'REGION': 'fsn1',  # fsn1=Falkenstein, nbg1=Nuremberg, hel1=Helsinki
    'ENDPOINT_URL': 'https://fsn1.your-objectstorage.com',
    'BUCKET_NAME': 'vochi',  # Ваше название bucket
    
    # Настройки для записей разговоров
    'RECORDINGS_PREFIX': 'call-recordings',
    'RETENTION_DAYS': 90,  # Сколько дней хранить записи
    
    # Настройки автоматического удаления
    'AUTO_CLEANUP_ENABLED': True,
    'CLEANUP_SCHEDULE': '0 2 * * *',  # Каждый день в 2:00
}

# URL для доступа к публичным файлам
def get_public_url(object_key: str) -> str:
    """Генерирует публичный URL для файла"""
    return f"https://{S3_CONFIG['BUCKET_NAME']}.{S3_CONFIG['REGION']}.your-objectstorage.com/{object_key}"

# Валидация настроек
def validate_s3_config() -> Dict[str, bool]:
    """Проверяет корректность настроек S3"""
    issues = []
    
    if 'ЗАМЕНИТЕ' in S3_CONFIG['ACCESS_KEY']:
        issues.append("ACCESS_KEY не настроен - замените на реальный ключ")
        
    if 'ЗАМЕНИТЕ' in S3_CONFIG['SECRET_KEY']:
        issues.append("SECRET_KEY не настроен - замените на реальный ключ")
        
    return {
        'valid': len(issues) == 0,
        'issues': issues
    }

# Инструкции по настройке
SETUP_INSTRUCTIONS = """
🔧 ИНСТРУКЦИЯ ПО НАСТРОЙКЕ:

1. Получите S3 credentials в Hetzner Console:
   → Откройте ваш проект в https://console.hetzner.com/
   → Перейдите в Security → S3 Credentials  
   → Нажмите "Generate credentials"
   → Скопируйте Access Key и Secret Key

2. Скопируйте этот файл:
   cp s3_config.example.py s3_config.py

3. Отредактируйте s3_config.py:
   → Замените 'ЗАМЕНИТЕ_НА_ВАШ_ACCESS_KEY' на ваш реальный Access Key
   → Замените 'ЗАМЕНИТЕ_НА_ВАШ_SECRET_KEY' на ваш реальный Secret Key
   → При необходимости измените REGION и BUCKET_NAME

4. Установите зависимости:
   pip install -r requirements.txt

5. Запустите тест:
   python test_s3_connection.py

📍 Ваш endpoint: fsn1.your-objectstorage.com
📍 Ваш bucket: vochi
📍 URL bucket: https://vochi.fsn1.your-objectstorage.com/

⚠️  БЕЗОПАСНОСТЬ:
- Файл s3_config.py уже добавлен в .gitignore
- НЕ коммитьте файл с реальными ключами в Git!
- Используйте переменные окружения в продакшене
"""

if __name__ == "__main__":
    print(SETUP_INSTRUCTIONS) 