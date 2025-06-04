#!/usr/bin/env python3
import requests
import urllib3
from datetime import datetime

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Конфигурация
BASE_URL = "http://91.149.128.210"
USERNAME = "root"
PASSWORD = "gjitkdjy4070+37529AAA"

def get_sms():
    session = requests.Session()
    
    # Устанавливаем заголовки как в браузере
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    session.headers.update(headers)
    
    # Сначала получаем страницу логина для получения возможных токенов
    try:
        login_page = session.get(f"{BASE_URL}/goip/en/index.php", verify=False)
        
        # Выполняем вход
        login_data = {
            "username": USERNAME,
            "password": PASSWORD,
            "Submit": "Sign in",
            "lan": "3"
        }
        
        login_response = session.post(
            f"{BASE_URL}/goip/en/dologin.php",
            data=login_data,
            verify=False,
            allow_redirects=True
        )
        
        print(f"Статус входа: {login_response.status_code}")
        
        # Получаем SMS
        sms_response = session.get(
            f"{BASE_URL}/goip/en/receive.php",
            params={"type": "1"},
            verify=False
        )
        
        print("\nОтвет сервера:")
        print("-" * 50)
        print(sms_response.text)
        print("-" * 50)
        
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    get_sms() 