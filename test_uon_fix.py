#!/usr/bin/env python3
import json
import time
import subprocess

print("1. Перезапускаю UON сервис...")
try:
    subprocess.run(["python3", "/root/asterisk-webhook/restart_uon.py"], timeout=30)
except:
    print("Restart завис, продолжаю...")

time.sleep(3)

print("2. Тестирую сохранение extensions...")
import requests

try:
    # Тест сохранения
    response = requests.post(
        "http://127.0.0.1:8022/uon-admin/api/save-extensions/0367",
        json={"extensions": {"4": "151", "2": "150"}},
        timeout=10
    )
    result = response.json()
    print(f"Save result: {result}")
    
    # Тест загрузки internal phones
    response2 = requests.get(
        "http://127.0.0.1:8022/uon-admin/api/internal-phones/0367",
        timeout=10
    )
    result2 = response2.json()
    print(f"Phones result: {result2}")
    
    with open("/root/asterisk-webhook/logs/uon_fix_test.json", "w") as f:
        json.dump({"save": result, "phones": result2}, f, indent=2)
    print("✅ Результаты записаны в logs/uon_fix_test.json")
    
except Exception as e:
    print(f"❌ Ошибка теста: {e}")
