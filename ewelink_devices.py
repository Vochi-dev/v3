#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Постоянный скрипт для работы с устройствами eWeLink
Сохраняет access token и позволяет получать устройства без повторной авторизации
"""

import json
import time
import requests
import hmac
import hashlib
import base64
import os
import random
import string
from datetime import datetime, timedelta

class EWeLinkDevices:
    """Клиент для работы с устройствами eWeLink"""
    
    def __init__(self):
        self.app_id = 'yjbs7ZRaIgNiqJ9uINiXjKcX01czdTdB'
        self.app_secret = 'tSK3T1tlnb2iNDGx31hhpyIeP34HFdQI'
        self.token_file = 'ewelink_token.json'
        self.devices_file = 'ewelink_devices.json'
        self.access_token = None
        self.refresh_token = None
        self.region = 'eu'
        
    def save_tokens(self, access_token, refresh_token, expires_in=7200):
        """Сохраняет токены в файл"""
        token_data = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_at': (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
            'region': self.region
        }
        
        with open(self.token_file, 'w') as f:
            json.dump(token_data, f, indent=2)
            
        print(f"💾 Токены сохранены в {self.token_file}")
        
    def load_tokens(self):
        """Загружает токены из файла"""
        if not os.path.exists(self.token_file):
            return False
            
        try:
            with open(self.token_file, 'r') as f:
                token_data = json.load(f)
                
            expires_at = datetime.fromisoformat(token_data['expires_at'])
            
            if datetime.now() < expires_at:
                self.access_token = token_data['access_token']
                self.refresh_token = token_data['refresh_token']
                self.region = token_data.get('region', 'eu')
                print(f"✅ Токены загружены из {self.token_file}")
                print(f"⏰ Действительны до: {expires_at}")
                return True
            else:
                print("⚠️ Токены истекли, нужна повторная авторизация")
                return False
                
        except Exception as e:
            print(f"❌ Ошибка загрузки токенов: {e}")
            return False
    
    def calculate_signature(self, data_string):
        """Вычисляет подпись для eWeLink API"""
        signature = base64.b64encode(
            hmac.new(
                self.app_secret.encode(),
                data_string.encode(),
                digestmod=hashlib.sha256
            ).digest()
        ).decode()
        return f"Sign {signature}"
    
    def generate_oauth_url(self):
        """Генерирует OAuth2 URL для авторизации"""
        # Генерируем случайные параметры
        nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        state = ''.join(random.choices(string.ascii_letters + string.digits, k=6)) 
        seq = str(int(time.time() * 1000))
        
        # Вычисляем подпись для OAuth2 URL
        message = f"{self.app_id}_{seq}"
        signature = base64.b64encode(
            hmac.new(
                self.app_secret.encode(),
                message.encode(),
                digestmod=hashlib.sha256
            ).digest()
        ).decode()
        
        # Создаем OAuth2 URL
        oauth_url = (
            f"https://c2ccdn.coolkit.cc/oauth/index.html"
            f"?clientId={self.app_id}"
            f"&seq={seq}"
            f"&authorization={signature}"
            f"&redirectUrl=https://httpbin.org/get"
            f"&grantType=authorization_code"
            f"&state={state}"
            f"&nonce={nonce}"
            f"&showQRCode=false"
        )
        
        print("🔗 Новая OAuth2 ссылка для авторизации:")
        print(oauth_url)
        print(f"\n📋 Параметры:")
        print(f"   State: {state}")
        print(f"   Nonce: {nonce}")
        print(f"   Seq: {seq}")
        print(f"\n⏰ Код будет действителен 30 секунд после авторизации!")
        print("🚀 Откройте ссылку в браузере и авторизуйтесь")
        
        return oauth_url
    
    def exchange_oauth_code(self, code, region='eu'):
        """Обменивает OAuth2 код на токены"""
        self.region = region
        token_url = f"https://{region}-apia.coolkit.cc/v2/user/oauth/token"
        
        data = {
            'code': code,
            'redirectUrl': 'https://httpbin.org/get',
            'grantType': 'authorization_code'
        }
        
        data_json = json.dumps(data, separators=(',', ':'))
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'eWeLink/4.9.2',
            'X-CK-Appid': self.app_id,
            'X-CK-Nonce': str(int(time.time() * 1000000))[:8],
            'X-CK-Timestamp': str(int(time.time())),
            'Authorization': self.calculate_signature(data_json)
        }
        
        try:
            response = requests.post(token_url, data=data_json, headers=headers, timeout=30)
            print(f"🔄 Обмен OAuth2 кода: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                if result.get('error') == 0:
                    data = result.get('data', {})
                    access_token = data.get('accessToken')
                    refresh_token = data.get('refreshToken')
                    expires_in = data.get('atExpiredTime', 7200)
                    
                    print(f"🔍 Debug: API ответ - {result}")
                    print(f"🔍 Debug: expires_in = {expires_in}")
                    
                    if access_token:
                        self.access_token = access_token
                        self.refresh_token = refresh_token
                        # Исправляем обработку времени истечения
                        try:
                            # atExpiredTime может быть в секундах или миллисекундах
                            if expires_in > 100000000000:  # Если больше 100 млрд, то это миллисекунды
                                expires_in = expires_in // 1000
                            # Если все еще слишком большое, используем дефолт
                            if expires_in > 86400 * 365:  # Больше года
                                expires_in = 7200  # 2 часа по умолчанию
                        except:
                            expires_in = 7200
                            
                        self.save_tokens(access_token, refresh_token, expires_in)
                        print("✅ Токены получены и сохранены!")
                        return True
                        
            print(f"❌ Ошибка обмена: {response.text}")
            return False
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return False
    
    def get_devices(self, save_to_file=True):
        """Получает список устройств"""
        if not self.access_token:
            print("❌ Нет access token. Нужна авторизация!")
            return None
            
        device_url = f"https://{self.region}-apia.coolkit.cc/v2/device/thing"
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'User-Agent': 'eWeLink/4.9.2',
            'X-CK-Appid': self.app_id
        }
        
        try:
            print(f"📱 Получаем устройства из региона {self.region}...")
            response = requests.get(device_url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('error') == 0:
                    devices = result.get('data', {}).get('thingList', [])
                    
                    if save_to_file:
                        with open(self.devices_file, 'w', encoding='utf-8') as f:
                            json.dump(devices, f, ensure_ascii=False, indent=2)
                        print(f"💾 Устройства сохранены в {self.devices_file}")
                    
                    self.print_device_summary(devices)
                    return devices
                else:
                    print(f"❌ API ошибка: {result}")
            else:
                print(f"❌ HTTP ошибка: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"❌ Ошибка получения устройств: {e}")
            
        return None
    
    def print_device_summary(self, devices):
        """Выводит краткую сводку устройств"""
        if not devices:
            print("❌ Устройства не найдены")
            return
            
        online_count = 0
        offline_count = 0
        
        print(f"\n🎉 НАЙДЕНО УСТРОЙСТВ: {len(devices)}")
        print("="*60)
        
        for i, device_item in enumerate(devices, 1):
            item_data = device_item.get('itemData', {})
            name = item_data.get('name', 'Без имени')
            device_id = item_data.get('deviceid', 'Unknown')
            online = item_data.get('online', False)
            brand = item_data.get('brandName', 'Unknown')
            
            if online:
                online_count += 1
            else:
                offline_count += 1
            
            status = "🟢 Онлайн" if online else "🔴 Офлайн"
            print(f"{i:2d}. {name:<20} ({brand}) - {status}")
        
        print("="*60)
        print(f"📊 Статистика: 🟢 {online_count} онлайн, 🔴 {offline_count} офлайн")
    
    def load_saved_devices(self):
        """Загружает сохраненные устройства из файла"""
        if os.path.exists(self.devices_file):
            try:
                with open(self.devices_file, 'r', encoding='utf-8') as f:
                    devices = json.load(f)
                print(f"📁 Загружены сохраненные устройства из {self.devices_file}")
                self.print_device_summary(devices)
                return devices
            except Exception as e:
                print(f"❌ Ошибка загрузки устройств: {e}")
        else:
            print(f"❌ Файл {self.devices_file} не найден")
        
        return None
    
    def get_device_status(self, device_id):
        """Получает текущий статус устройства"""
        if not self.access_token:
            print("❌ Нет access token. Нужна авторизация!")
            return None
            
        status_url = f"https://{self.region}-apia.coolkit.cc/v2/device/thing"
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'User-Agent': 'eWeLink/4.9.2',
            'X-CK-Appid': self.app_id
        }
        
        try:
            print(f"📊 Получаем статус устройства {device_id}...")
            response = requests.get(status_url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('error') == 0:
                    devices = result.get('data', {}).get('thingList', [])
                    
                    # Ищем нужное устройство по device_id
                    for device_item in devices:
                        item_data = device_item.get('itemData', {})
                        if item_data.get('deviceid') == device_id:
                            return item_data
                    
                    print(f"❌ Устройство {device_id} не найдено среди {len(devices)} устройств")
                    return None
                else:
                    print(f"❌ API ошибка: {result}")
            else:
                print(f"❌ HTTP ошибка: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"❌ Ошибка получения статуса: {e}")
            
        return None
    
    def toggle_device(self, device_id, state):
        """Включает/выключает устройство"""
        if not self.access_token:
            print("❌ Нет access token. Нужна авторизация!")
            return False
            
        control_url = f"https://{self.region}-apia.coolkit.cc/v2/device/thing/status"
        
        # Данные для управления устройством
        data = {
            'type': 1,
            'id': device_id,
            'params': {
                'switch': 'on' if state else 'off'
            }
        }
        
        data_json = json.dumps(data, separators=(',', ':'))
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'eWeLink/4.9.2',
            'X-CK-Appid': self.app_id,
            'Authorization': f'Bearer {self.access_token}'  # Используем Bearer токен вместо подписи
        }
        
        try:
            action = "включаем" if state else "выключаем"
            print(f"🔄 {action.capitalize()} устройство {device_id}...")
            
            response = requests.post(control_url, data=data_json, headers=headers, timeout=30)
            print(f"📊 Ответ сервера: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                if result.get('error') == 0:
                    print(f"✅ Устройство успешно {action}!")
                    return True
                else:
                    print(f"❌ Ошибка управления: {result}")
            else:
                print(f"❌ HTTP ошибка: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"❌ Ошибка управления устройством: {e}")
            
        return False
    
    def test_june(self):
        """Тестирует управление устройством June"""
        june_device_id = "10013a83f3"
        
        print("🧪 ТЕСТ УПРАВЛЕНИЯ УСТРОЙСТВОМ JUNE")
        print("="*50)
        
        # Получаем текущий статус
        print("\n1️⃣ Получаем текущий статус...")
        device_info = self.get_device_status(june_device_id)
        
        if not device_info:
            print("❌ Не удалось получить статус устройства")
            return False
            
        current_state = device_info.get('params', {}).get('switch', 'unknown')
        device_name = device_info.get('name', 'June')
        online = device_info.get('online', False)
        
        print(f"📱 Устройство: {device_name}")
        print(f"🔌 Текущий статус: {current_state}")
        print(f"🌐 Онлайн: {'✅' if online else '❌'}")
        
        if not online:
            print("❌ Устройство офлайн, управление невозможно!")
            return False
            
        # Переключаем в противоположное состояние
        new_state = current_state != 'on'
        action = "включение" if new_state else "выключение"
        
        print(f"\n2️⃣ Выполняем {action}...")
        if self.toggle_device(june_device_id, new_state):
            
            # Ждем 2 секунды и проверяем результат
            print("\n3️⃣ Ждем 2 секунды и проверяем результат...")
            time.sleep(2)
            
            updated_info = self.get_device_status(june_device_id)
            if updated_info:
                new_status = updated_info.get('params', {}).get('switch', 'unknown')
                expected = 'on' if new_state else 'off'
                
                if new_status == expected:
                    print(f"✅ УСПЕХ! Статус изменен: {current_state} → {new_status}")
                    
                    # Возвращаем обратно в исходное состояние
                    print(f"\n4️⃣ Возвращаем в исходное состояние ({current_state})...")
                    if self.toggle_device(june_device_id, current_state == 'on'):
                        print("✅ Устройство возвращено в исходное состояние")
                        return True
                    else:
                        print("⚠️ Не удалось вернуть в исходное состояние")
                        return False
                else:
                    print(f"❌ Статус не изменился: ожидали {expected}, получили {new_status}")
            else:
                print("❌ Не удалось проверить обновленный статус")
        
        return False

def main():
    """Основная функция"""
    client = EWeLinkDevices()
    
    print("🏠 eWeLink Device Manager")
    print("="*40)
    
    # Пробуем загрузить существующие токены
    if client.load_tokens():
        # Токены есть, получаем устройства
        devices = client.get_devices()
        if devices:
            print("\n✅ Устройства успешно получены с сохраненным токеном!")
            return devices
        else:
            print("❌ Не удалось получить устройства. Возможно токен истек.")
    
    # Если токенов нет или они не работают
    print("\n🔑 Нужна новая авторизация:")
    print("1. Получите новый OAuth2 код")
    print("2. Запустите: python3 ewelink_devices.py YOUR_OAUTH_CODE")
    print("\nИли загрузите ранее сохраненные устройства:")
    return client.load_saved_devices()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        
        if arg == "test-june":
            # Тестирование управления устройством June
            client = EWeLinkDevices()
            
            print("🔄 Загружаем токены для управления устройством...")
            if client.load_tokens():
                client.test_june()
            else:
                print("❌ Нет действительных токенов!")
                print("💡 Получите новый OAuth2 код и запустите:")
                print("   python3 ewelink_devices.py YOUR_OAUTH_CODE")
                print("🔗 Или сгенерируйте новую ссылку:")
                print("   python3 ewelink_devices.py generate-oauth-url")
                
        elif arg == "generate-oauth-url":
            # Генерация OAuth2 URL
            client = EWeLinkDevices()
            client.generate_oauth_url()
            
        elif arg.startswith(("test-device:", "toggle:")):
            # Управление произвольным устройством
            # Формат: test-device:DEVICE_ID или toggle:DEVICE_ID:on/off
            client = EWeLinkDevices()
            
            if client.load_tokens():
                if arg.startswith("test-device:"):
                    device_id = arg.split(":", 1)[1]
                    print(f"📊 Получаем статус устройства {device_id}...")
                    status = client.get_device_status(device_id)
                    if status:
                        switch_state = status.get('params', {}).get('switch', 'unknown')
                        print(f"🔌 Текущий статус: {switch_state}")
                    
                elif arg.startswith("toggle:"):
                    parts = arg.split(":")
                    if len(parts) == 3:
                        device_id = parts[1] 
                        state = parts[2].lower() == 'on'
                        client.toggle_device(device_id, state)
                    else:
                        print("❌ Неверный формат! Используйте: toggle:DEVICE_ID:on/off")
            else:
                print("❌ Нет действительных токенов!")
                
        else:
            # OAuth2 код
            code = arg
            client = EWeLinkDevices()
            
            print(f"🔄 Обмениваем OAuth2 код на токены...")
            if client.exchange_oauth_code(code):
                devices = client.get_devices()
                if devices:
                    print("\n🎉 УСПЕХ! Токены сохранены, устройства получены!")
                    print("\n💡 Теперь можете управлять устройствами:")
                    print("   python3 ewelink_devices.py test-june")
                    print("   python3 ewelink_devices.py toggle:DEVICE_ID:on")
                    print("   python3 ewelink_devices.py toggle:DEVICE_ID:off")
            else:
                print("❌ Не удалось обменять код на токены")
    else:
        # Обычный запуск - показ устройств
        result = main()
        if result:
            print("\n💡 Дополнительные команды:")
            print("🧪 Тест управления June:    python3 ewelink_devices.py test-june")
            print("📊 Статус устройства:      python3 ewelink_devices.py test-device:DEVICE_ID")
            print("🔄 Включить устройство:    python3 ewelink_devices.py toggle:DEVICE_ID:on")
            print("🔄 Выключить устройство:   python3 ewelink_devices.py toggle:DEVICE_ID:off")
            print("🔗 Новая OAuth2 ссылка:    python3 ewelink_devices.py generate-oauth-url") 