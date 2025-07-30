#!/usr/bin/env python3
"""
ФИНАЛЬНЫЙ ТЕСТ WEBSMS API
Действующий ключ: bOeR6LslKf
API активирован, баланс положительный
"""

import requests
import json
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('websms_test.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def test_websms_final():
    """Финальный тест WebSMS API"""
    
    # ДАННЫЕ
    USERNAME = "info@ead.by"
    API_KEY = "bOeR6LslKf"  # ДЕЙСТВУЮЩИЙ КЛЮЧ
    PHONE = "+375296254070"
    MESSAGE = f"FINAL TEST {datetime.now().strftime('%H:%M:%S')}"
    
    logger.info("=== ФИНАЛЬНЫЙ ТЕСТ WEBSMS API ===")
    logger.info(f"Username: {USERNAME}")
    logger.info(f"API Key: {API_KEY}")
    logger.info(f"Phone: {PHONE}")
    logger.info(f"Message: {MESSAGE}")
    
    # БАЗОВЫЙ ENDPOINT
    url = "https://cabinet.websms.by/api/send"
    logger.info(f"URL: {url}")
    
    # ПОПРОБУЕМ САМЫЕ ПРОСТЫЕ ФОРМАТЫ
    test_cases = [
        {
            "name": "Form Data (api_key)",
            "method": "POST",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "data": {
                "user": USERNAME,
                "api_key": API_KEY,
                "phone": PHONE,
                "text": MESSAGE
            },
            "send_as": "form"
        },
        {
            "name": "Form Data (apikey)", 
            "method": "POST",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "data": {
                "user": USERNAME,
                "apikey": API_KEY,
                "phone": PHONE,
                "text": MESSAGE
            },
            "send_as": "form"
        },
        {
            "name": "GET Parameters",
            "method": "GET", 
            "data": {
                "user": USERNAME,
                "api_key": API_KEY,
                "phone": PHONE,
                "text": MESSAGE
            },
            "send_as": "params"
        },
        {
            "name": "JSON (api_key)",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "data": {
                "user": USERNAME,
                "api_key": API_KEY,
                "phone": PHONE,
                "text": MESSAGE
            },
            "send_as": "json"
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        logger.info(f"\n--- ТЕСТ {i}: {test_case['name']} ---")
        
        try:
            if test_case["method"] == "GET":
                response = requests.get(
                    url, 
                    params=test_case["data"],
                    timeout=15
                )
            elif test_case["send_as"] == "form":
                response = requests.post(
                    url,
                    data=test_case["data"],
                    headers=test_case["headers"],
                    timeout=15
                )
            elif test_case["send_as"] == "json":
                response = requests.post(
                    url,
                    json=test_case["data"], 
                    headers=test_case["headers"],
                    timeout=15
                )
            
            logger.info(f"HTTP Status: {response.status_code}")
            logger.info(f"Response Headers: {dict(response.headers)}")
            logger.info(f"Response Text: {response.text}")
            
            # АНАЛИЗ ОТВЕТА
            if response.status_code == 200:
                try:
                    # Попробуем JSON
                    result = response.json()
                    logger.info(f"JSON Response: {json.dumps(result, ensure_ascii=False, indent=2)}")
                    
                    # ПРОВЕРЯЕМ УСПЕХ
                    if (isinstance(result, dict) and 
                        (result.get('status') == True or 
                         'id' in result or 
                         'messageId' in result or
                         result.get('success') == True)):
                        
                        logger.info("🎉 SMS УСПЕШНО ОТПРАВЛЕНО!")
                        print(f"\n🎉 SMS ОТПРАВЛЕНО УСПЕШНО!")
                        print(f"📋 Метод: {test_case['name']}")
                        print(f"📋 Ответ: {result}")
                        print(f"📱 ПРОВЕРЬТЕ ТЕЛЕФОН {PHONE}!")
                        return True
                        
                    # АНАЛИЗ ОШИБКИ
                    elif 'error' in result:
                        error = result['error']
                        if isinstance(error, dict):
                            error_code = error.get('code', 'unknown')
                            error_desc = error.get('description', 'unknown')
                            logger.error(f"API Error {error_code}: {error_desc}")
                        else:
                            logger.error(f"API Error: {error}")
                            
                except json.JSONDecodeError:
                    # ПРОВЕРЯЕМ ЧИСЛОВОЙ ОТВЕТ (ID сообщения)
                    if response.text.isdigit() and int(response.text) > 0:
                        logger.info("🎉 SMS УСПЕШНО ОТПРАВЛЕНО! (Numeric ID)")
                        print(f"\n🎉 SMS ОТПРАВЛЕНО! ID: {response.text}")
                        print(f"📋 Метод: {test_case['name']}")
                        print(f"📱 ПРОВЕРЬТЕ ТЕЛЕФОН {PHONE}!")
                        return True
                    else:
                        logger.warning(f"Non-JSON response: {response.text}")
                        
            else:
                logger.error(f"HTTP Error {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            
    logger.error("❌ ВСЕ ТЕСТЫ НЕУДАЧНЫ")
    return False

def main():
    print("🎯 ФИНАЛЬНЫЙ ТЕСТ WEBSMS API")
    print("="*50)
    print("📋 API ключ действующий: bOeR6LslKf")
    print("📋 API активирован, баланс положительный")
    print("📋 Логи сохраняются в websms_test.log")
    
    success = test_websms_final()
    
    if not success:
        print("\n❌ ОТПРАВКА НЕ УДАЛАСЬ")
        print("📋 Детальные логи в websms_test.log")
        print("📋 Данные для отправки владельцу сервиса готовы")
    
    print(f"\n📄 Лог файл: websms_test.log")

if __name__ == "__main__":
    main() 