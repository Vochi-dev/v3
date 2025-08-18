#!/usr/bin/env python3
import subprocess
import sys
import time

try:
    # Останавливаем
    print("Останавливаю uon...")
    subprocess.run(["/root/asterisk-webhook/uon.sh", "stop"], timeout=10)
    time.sleep(2)
    
    # Запускаем
    print("Запускаю uon...")
    subprocess.run(["/root/asterisk-webhook/uon.sh", "start"], timeout=10)
    print("✅ uon перезапущен")
    
except subprocess.TimeoutExpired:
    print("❌ Команда зависла")
except Exception as e:
    print(f"❌ Ошибка: {e}")
