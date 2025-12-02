#!/usr/bin/env python3
"""
Тестовый скрипт для эмуляции трёх типов звонков.
Отправляет события на сервер точно так же, как это делает хост.

Три типа звонков:
1. Обычный исходящий (CallType=1, ExternalInitiated=false)
2. CRM исходящий (CallType=1, ExternalInitiated=true)
3. Входящий (CallType=0, ExternalInitiated=true - ошибка хоста)
"""

import requests
import time
import uuid
import sys

# Конфигурация
SERVER_URL = "http://localhost:8000"
TOKEN = "375293332255"  # Токен предприятия 0367

# Тестовые данные
EXTERNAL_PHONE = "375447034448"
INTERNAL_EXT = "151"
TRUNK = "0001363"


def send_event(endpoint: str, data: dict):
    """Отправляет событие на сервер."""
    url = f"{SERVER_URL}/{endpoint}"
    try:
        response = requests.post(url, json=data, timeout=10)
        print(f"  [{endpoint}] -> {response.status_code}")
        return response
    except Exception as e:
        print(f"  [{endpoint}] ERROR: {e}")
        return None


def generate_unique_id():
    """Генерирует UniqueId в формате Asterisk."""
    timestamp = int(time.time())
    counter = int(time.time() * 1000) % 1000
    return f"{timestamp}.{counter}"


def generate_bridge_uniqueid():
    """Генерирует BridgeUniqueid."""
    return str(uuid.uuid4())


def test_regular_outgoing():
    """
    ТЕСТ 1: Обычный исходящий звонок
    - CallType=1
    - ExternalInitiated=false (отсутствует)
    """
    print("\n" + "="*60)
    print("ТЕСТ 1: Обычный исходящий звонок")
    print("="*60)
    
    uid1 = generate_unique_id()
    time.sleep(0.1)
    uid2 = generate_unique_id()
    bridge_uid = generate_bridge_uniqueid()
    
    print(f"UniqueId главный: {uid1}")
    print(f"UniqueId второй: {uid2}")
    print(f"BridgeUniqueid: {bridge_uid}")
    
    # 1. dial
    print("\n1. Отправляем dial...")
    send_event("dial", {
        "Extensions": [INTERNAL_EXT],
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "ExtTrunk": "",
        "ExtPhone": "",
        "Trunk": TRUNK,
        "UniqueId": uid1,
        "CallType": 1
    })
    time.sleep(1)
    
    # 2. new_callerid
    print("2. Отправляем new_callerid...")
    send_event("new_callerid", {
        "ConnectedLineNum": INTERNAL_EXT,
        "Channel": f"SIP/{TRUNK}-00000001",
        "CallerIDNum": EXTERNAL_PHONE,
        "ConnectedLineName": INTERNAL_EXT,
        "Token": TOKEN,
        "Exten": EXTERNAL_PHONE,
        "CallerIDName": "<unknown>",
        "Context": "from-out-office",
        "UniqueId": uid2
    })
    time.sleep(1)
    
    # 3. bridge_create
    print("3. Отправляем bridge_create...")
    send_event("bridge_create", {
        "BridgeType": "",
        "BridgeName": "<unknown>",
        "BridgeTechnology": "simple_bridge",
        "Token": TOKEN,
        "BridgeCreator": "<unknown>",
        "BridgeUniqueid": bridge_uid,
        "UniqueId": "",
        "BridgeNumChannels": "0"
    })
    time.sleep(0.5)
    
    # 4. bridge #1 (caller=internal, Exten=external) - ЭТОТ НУЖЕН!
    print("4. Отправляем bridge #1 (internal caller, external Exten)...")
    send_event("bridge", {
        "ConnectedLineNum": "<unknown>",
        "Channel": f"SIP/{INTERNAL_EXT}-00000001",
        "CallerIDNum": INTERNAL_EXT,
        "ConnectedLineName": "<unknown>",
        "Token": TOKEN,
        "BridgeUniqueid": bridge_uid,
        "Exten": EXTERNAL_PHONE,  # <-- Внешний номер!
        "CallerIDName": INTERNAL_EXT,
        "UniqueId": uid1
    })
    time.sleep(0.5)
    
    # 5. bridge #2 (caller=external, connected=internal) - ПРОПУСКАЕМ
    print("5. Отправляем bridge #2 (external caller, internal connected)...")
    send_event("bridge", {
        "ConnectedLineNum": INTERNAL_EXT,
        "Channel": f"SIP/{TRUNK}-00000001",
        "CallerIDNum": EXTERNAL_PHONE,
        "ConnectedLineName": INTERNAL_EXT,
        "Token": TOKEN,
        "BridgeUniqueid": bridge_uid,
        "Exten": "",  # <-- Пустой!
        "CallerIDName": "",
        "UniqueId": uid2
    })
    time.sleep(3)
    
    # 6. hangup
    print("6. Отправляем hangup...")
    send_event("hangup", {
        "StartTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "DateReceived": time.strftime("%Y-%m-%d %H:%M:%S"),
        "Extensions": [INTERNAL_EXT],
        "CallStatus": "2",
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "Trunk": TRUNK,
        "EndTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "UniqueId": uid1,
        "CallType": 1
    })
    
    print("\n✅ Тест 1 завершён")


def test_crm_outgoing():
    """
    ТЕСТ 2: CRM исходящий звонок (внешняя инициация)
    - CallType=1
    - ExternalInitiated=true
    """
    print("\n" + "="*60)
    print("ТЕСТ 2: CRM исходящий звонок (ExternalInitiated=true)")
    print("="*60)
    
    uid_internal = generate_unique_id()
    time.sleep(0.1)
    uid_main = generate_unique_id()
    time.sleep(0.1)
    uid_external = generate_unique_id()
    bridge_uid1 = generate_bridge_uniqueid()
    bridge_uid2 = generate_bridge_uniqueid()
    
    print(f"UniqueId internal: {uid_internal}")
    print(f"UniqueId main: {uid_main}")
    print(f"UniqueId external: {uid_external}")
    
    # 1. bridge_create #1 (промежуточный)
    print("\n1. Отправляем bridge_create #1 (промежуточный)...")
    send_event("bridge_create", {
        "BridgeType": "",
        "BridgeName": "<unknown>",
        "BridgeTechnology": "simple_bridge",
        "Token": TOKEN,
        "BridgeCreator": "<unknown>",
        "ExternalInitiated": True,
        "BridgeUniqueid": bridge_uid1,
        "UniqueId": "",
        "BridgeNumChannels": "0"
    })
    time.sleep(0.5)
    
    # 2. bridge #1 (internal→external) - ПРОПУСКАЕМ
    print("2. Отправляем bridge #1 (internal→external, промежуточный)...")
    send_event("bridge", {
        "ConnectedLineNum": EXTERNAL_PHONE,
        "Channel": f"SIP/{INTERNAL_EXT}-00000001",
        "CallerIDNum": INTERNAL_EXT,
        "ConnectedLineName": "Тестовый покупатель",
        "Token": TOKEN,
        "ExternalInitiated": True,
        "BridgeUniqueid": bridge_uid1,
        "Exten": "",
        "CallerIDName": "",
        "UniqueId": uid_internal
    })
    time.sleep(0.5)
    
    # 3. dial
    print("3. Отправляем dial...")
    send_event("dial", {
        "Extensions": [INTERNAL_EXT],
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "ExtTrunk": "",
        "ExternalInitiated": True,
        "ExtPhone": "",
        "Trunk": TRUNK,
        "UniqueId": uid_main,
        "CallType": 1
    })
    time.sleep(1)
    
    # 4. new_callerid
    print("4. Отправляем new_callerid...")
    send_event("new_callerid", {
        "ConnectedLineNum": INTERNAL_EXT,
        "Channel": f"SIP/{TRUNK}-00000002",
        "CallerIDNum": EXTERNAL_PHONE,
        "ConnectedLineName": "<unknown>",
        "Token": TOKEN,
        "ExternalInitiated": True,
        "Exten": EXTERNAL_PHONE,
        "CallerIDName": "<unknown>",
        "Context": "from-out-office",
        "UniqueId": uid_external
    })
    time.sleep(1)
    
    # 5. bridge #2 (external→internal) - ЭТОТ НУЖЕН!
    print("5. Отправляем bridge #2 (external→internal, основной)...")
    send_event("bridge", {
        "ConnectedLineNum": INTERNAL_EXT,
        "Channel": f"SIP/{TRUNK}-00000002",
        "CallerIDNum": EXTERNAL_PHONE,
        "ConnectedLineName": "<unknown>",
        "Token": TOKEN,
        "ExternalInitiated": True,
        "BridgeUniqueid": bridge_uid2,
        "Exten": "",
        "CallerIDName": "",
        "UniqueId": uid_external
    })
    time.sleep(3)
    
    # 6. hangup (основной)
    print("6. Отправляем hangup (основной, CallType=1)...")
    send_event("hangup", {
        "StartTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "DateReceived": time.strftime("%Y-%m-%d %H:%M:%S"),
        "Extensions": [""],
        "CallStatus": "2",
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "ExternalInitiated": True,
        "Trunk": TRUNK,
        "EndTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "UniqueId": uid_main,
        "CallType": 1
    })
    time.sleep(0.5)
    
    # 7. hangup (паразитный, CallType=2) - должен игнорироваться
    print("7. Отправляем hangup (паразитный, CallType=2)...")
    send_event("hangup", {
        "StartTime": "",
        "DateReceived": time.strftime("%Y-%m-%d %H:%M:%S"),
        "Extensions": [INTERNAL_EXT],
        "CallStatus": "2",
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "ExternalInitiated": True,
        "EndTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "UniqueId": uid_internal,
        "CallType": 2
    })
    
    print("\n✅ Тест 2 завершён")


def test_incoming():
    """
    ТЕСТ 3: Входящий звонок
    - CallType=0
    - ExternalInitiated=true (ОШИБКА ХОСТА, но так приходит)
    """
    print("\n" + "="*60)
    print("ТЕСТ 3: Входящий звонок (CallType=0)")
    print("="*60)
    
    uid_main = generate_unique_id()
    time.sleep(0.1)
    uid_internal = generate_unique_id()
    bridge_uid = generate_bridge_uniqueid()
    
    print(f"UniqueId main: {uid_main}")
    print(f"UniqueId internal: {uid_internal}")
    print(f"BridgeUniqueid: {bridge_uid}")
    
    # 1. start
    print("\n1. Отправляем start...")
    send_event("start", {
        "UniqueId": uid_main,
        "Token": TOKEN,
        "ExternalInitiated": True,  # Ошибка хоста!
        "CallType": 0,
        "Phone": EXTERNAL_PHONE,
        "Trunk": TRUNK
    })
    time.sleep(1)
    
    # 2. new_callerid
    print("2. Отправляем new_callerid...")
    send_event("new_callerid", {
        "ConnectedLineNum": "<unknown>",
        "Channel": f"SIP/{TRUNK}-00000003",
        "CallerIDNum": EXTERNAL_PHONE,
        "ConnectedLineName": "<unknown>",
        "Token": TOKEN,
        "ExternalInitiated": True,
        "Exten": TRUNK,  # <-- Trunk, не внешний номер!
        "CallerIDName": f"-{EXTERNAL_PHONE}",
        "Context": "from-out-office",
        "UniqueId": uid_main
    })
    time.sleep(0.5)
    
    # 3. dial
    print("3. Отправляем dial...")
    send_event("dial", {
        "Extensions": ["150", INTERNAL_EXT, "152"],
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "ExtTrunk": "",
        "ExternalInitiated": True,
        "ExtPhone": EXTERNAL_PHONE,
        "Trunk": TRUNK,
        "UniqueId": uid_main,
        "CallType": 0
    })
    time.sleep(2)
    
    # 4. bridge_create
    print("4. Отправляем bridge_create...")
    send_event("bridge_create", {
        "BridgeType": "",
        "BridgeName": "<unknown>",
        "BridgeTechnology": "simple_bridge",
        "Token": TOKEN,
        "BridgeCreator": "<unknown>",
        "ExternalInitiated": True,
        "BridgeUniqueid": bridge_uid,
        "UniqueId": "",
        "BridgeNumChannels": "0"
    })
    time.sleep(0.5)
    
    # 5. bridge #1 (internal→external) - ПРОПУСКАЕМ (промежуточный)
    print("5. Отправляем bridge #1 (internal→external)...")
    send_event("bridge", {
        "ConnectedLineNum": EXTERNAL_PHONE,
        "Channel": f"SIP/{INTERNAL_EXT}-00000003",
        "CallerIDNum": INTERNAL_EXT,
        "ConnectedLineName": f"-{EXTERNAL_PHONE}",
        "Token": TOKEN,
        "ExternalInitiated": True,
        "BridgeUniqueid": bridge_uid,
        "Exten": "",
        "CallerIDName": "",
        "UniqueId": uid_internal
    })
    time.sleep(0.5)
    
    # 6. bridge #2 (external→internal, Exten=trunk) - ЭТОТ НУЖЕН!
    print("6. Отправляем bridge #2 (external→internal, Exten=trunk)...")
    send_event("bridge", {
        "ConnectedLineNum": INTERNAL_EXT,
        "Channel": f"SIP/{TRUNK}-00000003",
        "CallerIDNum": EXTERNAL_PHONE,
        "ConnectedLineName": "<unknown>",
        "Token": TOKEN,
        "ExternalInitiated": True,
        "BridgeUniqueid": bridge_uid,
        "Exten": TRUNK,  # <-- Trunk, не внешний номер!
        "CallerIDName": f"-{EXTERNAL_PHONE}",
        "UniqueId": uid_main
    })
    time.sleep(3)
    
    # 7. hangup
    print("7. Отправляем hangup...")
    send_event("hangup", {
        "StartTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "DateReceived": time.strftime("%Y-%m-%d %H:%M:%S"),
        "Extensions": [INTERNAL_EXT],
        "CallStatus": "2",
        "Phone": EXTERNAL_PHONE,
        "Token": TOKEN,
        "ExternalInitiated": True,
        "Trunk": TRUNK,
        "EndTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "UniqueId": uid_main,
        "CallType": 0
    })
    
    print("\n✅ Тест 3 завершён")


def main():
    print("="*60)
    print("КОМПЛЕКСНЫЙ ТЕСТ ТРЁХ ТИПОВ ЗВОНКОВ")
    print("="*60)
    print(f"Сервер: {SERVER_URL}")
    print(f"Токен: {TOKEN}")
    print(f"Внешний номер: {EXTERNAL_PHONE}")
    print(f"Внутренний номер: {INTERNAL_EXT}")
    print(f"Trunk: {TRUNK}")
    
    if len(sys.argv) > 1:
        test_num = sys.argv[1]
        if test_num == "1":
            test_regular_outgoing()
        elif test_num == "2":
            test_crm_outgoing()
        elif test_num == "3":
            test_incoming()
        else:
            print(f"Неизвестный тест: {test_num}")
            print("Использование: python test_three_call_types.py [1|2|3|all]")
    else:
        # Запускаем все тесты
        test_regular_outgoing()
        time.sleep(5)
        test_crm_outgoing()
        time.sleep(5)
        test_incoming()
    
    print("\n" + "="*60)
    print("ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("Проверьте Telegram для результатов!")
    print("="*60)


if __name__ == "__main__":
    main()

