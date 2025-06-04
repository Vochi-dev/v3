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

def get_sms_messages():
    print("\nПолучаем SMS сообщения...")
    try:
        session = requests.Session()
        
        # Шаг 1: Получаем форму логина и возможные скрытые поля
        print("Шаг 1: Получаем форму логина...")
        response = session.get(f"{BASE_URL}/goip/en/index.php", verify=False, timeout=5)
        print(f"Статус получения формы: {response.status_code}")
        
        # Шаг 2: Выполняем вход
        print("\nШаг 2: Выполняем вход...")
        login_data = {
            "username": USERNAME,
            "password": PASSWORD,
            "Submit": "Sign in",
            "lan": "3"
        }
        
        response = session.post(
            f"{BASE_URL}/goip/en/dologin.php",
            data=login_data,
            verify=False,
            timeout=5,
            allow_redirects=True
        )
        print(f"Статус входа: {response.status_code}")
        print(f"URL после входа: {response.url}")
        
        # Шаг 3: Получаем SMS с сохраненными cookies
        print("\nШаг 3: Получаем SMS...")
        
        # Пробуем разные URL для получения SMS
        urls = [
            ("/goip/en/receive.php", {}),
            ("/goip/en/receive.php", {"type": "1"}),  # тип 1 - входящие
            ("/goip/en/receive.php", {"type": "2"}),  # тип 2 - исходящие
            ("/goip/en/receivebox.php", {}),
            ("/goip/en/receivebox.php", {"type": "inbox"})
        ]
        
        for url, params in urls:
            print(f"\nПробуем URL: {url}")
            print(f"С параметрами: {params}")
            try:
                response = session.get(
                    f"{BASE_URL}{url}",
                    params=params,
                    verify=False,
                    timeout=5
                )
                print(f"Статус: {response.status_code}")
                print(f"Content-Type: {response.headers.get('Content-Type', 'не указан')}")
                print(f"Длина ответа: {len(response.text)}")
                print("\nПервые 500 символов ответа:")
                print(response.text[:500])
                print("-" * 50)
                
                # Если в ответе нет формы логина, значит мы получили правильный ответ
                if "login" not in response.text.lower() and len(response.text) > 100:
                    print("\nПохоже, мы получили правильный ответ!")
                    print("Полный ответ:")
                    print(response.text)
                    break
                    
            except requests.exceptions.Timeout:
                print("Таймаут запроса")
            except Exception as e:
                print(f"Ошибка запроса: {e}")
                
    except Exception as e:
        print(f"Общая ошибка: {e}")

if __name__ == "__main__":
    print("Тестирование GoIP SMS API...")
    print(f"URL: {BASE_URL}")
    print(f"Пользователь: {USERNAME}")
    print("-" * 50)
    
    get_sms_messages() 