#!/usr/bin/env python3
"""
Скрипт автоматического обновления токенов eWeLink
Запускается через cron каждые 3 дня
"""

import sys
import os
import json
import logging
from datetime import datetime, timezone, timedelta

# Добавляем текущую директорию в путь
sys.path.append('/root/asterisk-webhook')

from ewelink_devices import EWeLinkDevices

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/root/asterisk-webhook/token_refresh.log'),
        logging.StreamHandler()
    ]
)

def check_token_expiry():
    """Проверяет срок действия токенов"""
    try:
        with open('/root/asterisk-webhook/ewelink_token.json', 'r') as f:
            tokens = json.load(f)
            
        expires_at_str = tokens.get('expires_at')
        if not expires_at_str:
            logging.warning("Нет информации о сроке действия токена")
            return True  # Обновляем на всякий случай
            
        expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
            
        now = datetime.now(timezone.utc)
        time_left = expires_at - now
        
        logging.info(f"Токен истекает: {expires_at}")
        logging.info(f"Времени осталось: {time_left}")
        
        # Обновляем если меньше 2 дней осталось
        return time_left.total_seconds() < (2 * 24 * 3600)
        
    except Exception as e:
        logging.error(f"Ошибка проверки токена: {e}")
        return True

def refresh_tokens():
    """Обновляет токены через refresh_token"""
    try:
        device_client = EWeLinkDevices()
        
        if device_client.refresh_access_token():
            logging.info("✅ Токены успешно обновлены через refresh_token!")
            return True
        else:
            logging.error("❌ Не удалось обновить токены через refresh_token")
            return False
            
    except Exception as e:
        logging.error(f"❌ Ошибка обновления токенов: {e}")
        return False

def main():
    """Основная функция"""
    logging.info("🔄 Запуск проверки токенов eWeLink...")
    
    if check_token_expiry():
        logging.info("🕐 Токены требуют обновления")
        
        if refresh_tokens():
            logging.info("🎉 Токены успешно обновлены!")
            sys.exit(0)
        else:
            logging.error("💥 Не удалось обновить токены автоматически!")
            logging.error("📞 Требуется ручная авторизация OAuth!")
            sys.exit(1)
    else:
        logging.info("✅ Токены еще действительны")
        sys.exit(0)

if __name__ == "__main__":
    main() 