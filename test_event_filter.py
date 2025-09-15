#!/usr/bin/env python3
"""
Тестирование системы фильтрации событий EventFilter

Проверяет работу на примерах из CallsManual_V2
"""

import sys
import os
import asyncio
from datetime import datetime

# Добавляем путь к проекту
sys.path.append('/root/asterisk-webhook')

from app.services.calls.event_filter import (
    EventFilter, 
    Event, 
    create_event_from_asterisk_data,
    create_events_from_call_sequence
)

def test_simple_incoming_call():
    """
    Тестирует простой входящий звонок (тип 2-1)
    start → dial → bridge → hangup
    """
    print("🧪 Тест простого входящего звонка (2-1)")
    
    # Симулируем последовательность событий из CallsManual_V2/2-1
    call_sequence = [
        ('start', {
            'UniqueId': '1757772742.60',
            'CallType': 0,
            'Phone': '375296254070',
            'Token': '375293332255',
            'Trunk': '0001363'
        }),
        ('dial', {
            'UniqueId': '1757772742.60',
            'CallType': 0,
            'Phone': '375296254070',
            'Extensions': ['150', '151', '152'],
            'Token': '375293332255'
        }),
        ('bridge', {
            'UniqueId': '1757772742.60',
            'CallerIDNum': '375296254070',
            'ConnectedLineNum': '151',
            'BridgeUniqueid': '77c13775-bede-4f37-886b-86a7dcf0b06a',
            'Token': '375293332255'
        }),
        ('hangup', {
            'UniqueId': '1757772742.60',
            'CallType': 0,
            'CallStatus': '2',
            'Phone': '375296254070',
            'Extensions': ['151'],
            'Token': '375293332255'
        })
    ]
    
    # Создаем события
    events = create_events_from_call_sequence(call_sequence)
    
    # Фильтруем
    event_filter = EventFilter()
    result = event_filter.filter_events_for_integrations(events)
    
    # Проверяем результат
    print(f"📊 Результат фильтрации:")
    print(f"   Сложность: {result.complexity}")
    print(f"   Основной UID: {result.primary_uid}")
    print(f"   Bitrix24: {len(result.bitrix24)} событий")
    print(f"   Telegram: {len(result.telegram)} событий")
    print(f"   CRM: {len(result.crm)} событий")
    
    # Ожидаемые результаты
    assert result.complexity == "SIMPLE", f"Ожидали SIMPLE, получили {result.complexity}"
    assert result.primary_uid == "1757772742.60", f"Неверный primary_uid: {result.primary_uid}"
    assert len(result.bitrix24) == 3, f"Для Bitrix24 ожидали 3 события, получили {len(result.bitrix24)}"
    assert len(result.telegram) == 2, f"Для Telegram ожидали 2 события, получили {len(result.telegram)}"
    
    # Проверяем типы событий для Bitrix24
    b24_types = [e.event for e in result.bitrix24]
    expected_b24 = ['start', 'bridge', 'hangup']
    assert b24_types == expected_b24, f"Неверная последовательность B24: {b24_types} vs {expected_b24}"
    
    # Проверяем типы событий для Telegram
    tg_types = [e.event for e in result.telegram]
    expected_tg = ['start', 'hangup']
    assert tg_types == expected_tg, f"Неверная последовательность TG: {tg_types} vs {expected_tg}"
    
    print("✅ Тест простого входящего звонка ПРОЙДЕН")
    return True

def test_simple_outgoing_call():
    """
    Тестирует простой исходящий звонок (тип 1-1)
    dial → bridge → hangup
    """
    print("\n🧪 Тест простого исходящего звонка (1-1)")
    
    call_sequence = [
        ('dial', {
            'UniqueId': '1757765248.0',
            'Phone': '375296254070',
            'Extensions': ['151'],
            'CallType': 1,
            'Token': '375293332255'
        }),
        ('bridge', {
            'UniqueId': '1757765248.0',
            'CallerIDNum': '151',
            'ConnectedLineNum': '375296254070',
            'BridgeUniqueid': '6d2cd650-65a3-4b24-96bd-ac84c0222a82',
            'Token': '375293332255'
        }),
        ('hangup', {
            'UniqueId': '1757765248.0',
            'CallType': 1,
            'CallStatus': '2',
            'Phone': '375296254070',
            'Extensions': ['151'],
            'Token': '375293332255'
        })
    ]
    
    events = create_events_from_call_sequence(call_sequence)
    
    event_filter = EventFilter()
    result = event_filter.filter_events_for_integrations(events)
    
    print(f"📊 Результат фильтрации:")
    print(f"   Сложность: {result.complexity}")
    print(f"   Основной UID: {result.primary_uid}")
    print(f"   Bitrix24: {len(result.bitrix24)} событий")
    print(f"   Telegram: {len(result.telegram)} событий")
    
    # Проверки
    assert result.complexity == "SIMPLE"
    assert result.primary_uid == "1757765248.0"
    assert len(result.bitrix24) == 3  # dial, bridge, hangup
    assert len(result.telegram) == 2  # dial, hangup
    
    print("✅ Тест простого исходящего звонка ПРОЙДЕН")
    return True

def test_complex_call_detection():
    """
    Тестирует определение сложных типов звонков
    """
    print("\n🧪 Тест определения сложности звонков")
    
    event_filter = EventFilter()
    
    # Тест 1: FollowMe (много событий)
    followme_events = []
    for i in range(40):  # 40 событий
        event = Event('bridge', f'uid_{i}', datetime.now(), {})
        followme_events.append(event)
    
    complexity = event_filter.get_call_complexity(followme_events)
    assert complexity == "FOLLOWME", f"Ожидали FOLLOWME, получили {complexity}"
    print("✅ FOLLOWME определен правильно")
    
    # Тест 2: Множественный перевод (много мостов)
    transfer_events = []
    for i in range(6):  # 6 bridge событий
        event = Event('bridge', f'uid_{i}', datetime.now(), {})
        transfer_events.append(event)
    
    complexity = event_filter.get_call_complexity(transfer_events)
    assert complexity == "MULTIPLE_TRANSFER", f"Ожидали MULTIPLE_TRANSFER, получили {complexity}"
    print("✅ MULTIPLE_TRANSFER определен правильно")
    
    # Тест 3: Занятый менеджер (множественные start)
    busy_events = [
        Event('start', 'uid_1', datetime.now(), {}),
        Event('start', 'uid_2', datetime.now(), {}),
        Event('bridge', 'uid_1', datetime.now(), {})
    ]
    
    complexity = event_filter.get_call_complexity(busy_events)
    assert complexity == "BUSY_MANAGER", f"Ожидали BUSY_MANAGER, получили {complexity}"
    print("✅ BUSY_MANAGER определен правильно")
    
    print("✅ Тест определения сложности ПРОЙДЕН")
    return True

def test_multiple_transfer_call():
    """
    Тестирует множественный перевод A→B→C (тип 2-18)
    Основан на реальных данных из CallsManual_V2/2-18
    """
    print("\n🧪 Тест множественного перевода A→B→C (2-18)")
    
    # Упрощенная последовательность ключевых событий из 2-18
    call_sequence = [
        ('start', {
            'UniqueId': '1757840723.6',  # ОСНОВНОЙ UID
            'CallType': 0,
            'Phone': '375447034448',
            'Token': '375293332255'
        }),
        ('dial', {
            'UniqueId': '1757840723.6',
            'Extensions': ['150', '151', '152'],
            'Token': '375293332255'
        }),
        # Первый bridge - внешний + 151
        ('bridge', {
            'UniqueId': '1757840723.6',
            'CallerIDNum': '375447034448',
            'ConnectedLineNum': '151',
            'BridgeUniqueid': 'c50ef955-2780-4484-a218-dfb1db61b5bc',
            'Token': '375293332255'
        }),
        # Bridge консультации 151→152 (пропускаем детали)
        ('bridge', {
            'UniqueId': '1757840735.11',
            'CallerIDNum': '151',
            'ConnectedLineNum': '152',
            'BridgeUniqueid': 'a54508e3-cc4d-4202-9a2f-024033d0cf87',
            'Token': '375293332255'
        }),
        # Bridge внешний переключился на 152
        ('bridge', {
            'UniqueId': '1757840723.6',  # ОСНОВНОЙ UID снова
            'CallerIDNum': '375447034448',
            'ConnectedLineNum': '152',
            'BridgeUniqueid': 'a54508e3-cc4d-4202-9a2f-024033d0cf87',
            'Token': '375293332255'
        }),
        # Bridge консультации 152→150 (пропускаем детали)
        ('bridge', {
            'UniqueId': '1757840756.17',
            'CallerIDNum': '152',
            'ConnectedLineNum': '150',
            'BridgeUniqueid': '6f5224d4-0bb9-42f4-9150-5c6d72e6a4d8',
            'Token': '375293332255'
        }),
        # Финальный bridge - внешний + 150
        ('bridge', {
            'UniqueId': '1757840757.18',
            'CallerIDNum': '150',
            'ConnectedLineNum': '375447034448',
            'BridgeUniqueid': 'a54508e3-cc4d-4202-9a2f-024033d0cf87',
            'Token': '375293332255'
        }),
        # Промежуточный hangup (консультация)
        ('hangup', {
            'UniqueId': '1757840735.11',
            'CallStatus': '2',
            'CallType': 2,
            'Extensions': ['152'],
            'Token': '375293332255'
        }),
        # Промежуточный hangup (консультация)
        ('hangup', {
            'UniqueId': '1757840756.17',
            'CallStatus': '2',
            'CallType': 2,
            'Extensions': ['150'],
            'Token': '375293332255'
        }),
        # Финальный hangup ОСНОВНОГО звонка
        ('hangup', {
            'UniqueId': '1757840723.6',  # ОСНОВНОЙ UID
            'CallStatus': '2',
            'CallType': 0,
            'Phone': '375447034448',
            'Extensions': ['151'],  # Начинал с 151
            'Token': '375293332255'
        })
    ]
    
    events = create_events_from_call_sequence(call_sequence)
    
    event_filter = EventFilter()
    result = event_filter.filter_events_for_integrations(events)
    
    print(f"📊 Результат фильтрации:")
    print(f"   Сложность: {result.complexity}")
    print(f"   Основной UID: {result.primary_uid}")
    print(f"   Bitrix24: {len(result.bitrix24)} событий")
    print(f"   Telegram: {len(result.telegram)} событий")
    
    # Проверки для множественного перевода
    assert result.complexity == "MULTIPLE_TRANSFER", f"Ожидали MULTIPLE_TRANSFER, получили {result.complexity}"
    assert result.primary_uid == "1757840723.6", f"Неверный primary_uid: {result.primary_uid}"
    
    # Для множественного перевода ожидаем больше событий в Bitrix24
    assert len(result.bitrix24) >= 3, f"Для MULTIPLE_TRANSFER B24 ожидали >= 3 событий, получили {len(result.bitrix24)}"
    assert len(result.telegram) == 2, f"Для Telegram ожидали 2 события, получили {len(result.telegram)}"
    
    # Проверяем что есть start и hangup для основного UID
    b24_events = result.bitrix24
    has_start = any(e.event == 'start' and e.uniqueid == '1757840723.6' for e in b24_events)
    has_hangup = any(e.event == 'hangup' and e.uniqueid == '1757840723.6' for e in b24_events)
    has_bridge = any(e.event == 'bridge' for e in b24_events)
    
    assert has_start, "Нет start события для основного UID в Bitrix24"
    assert has_hangup, "Нет hangup события для основного UID в Bitrix24"
    assert has_bridge, "Нет bridge событий в Bitrix24"
    
    print("✅ Тест множественного перевода A→B→C ПРОЙДЕН")
    return True

def test_busy_manager_call():
    """
    Тестирует звонки к занятым менеджерам (тип 2-19)
    Основан на реальных данных из CallsManual_V2/2-19
    """
    print("\n🧪 Тест звонков к занятым менеджерам (2-19)")
    
    # Упрощенная последовательность ключевых событий из 2-19
    call_sequence = [
        # ВНУТРЕННИЙ РАЗГОВОР УЖЕ ИДЕТ (150↔152)
        ('bridge', {
            'UniqueId': '1757841094.32',  # 150-й канал
            'CallerIDNum': '150',
            'ConnectedLineNum': '152',
            'BridgeUniqueid': '45ec1f12-8845-48f6-bb56-7090be11cf3a',
            'Token': '375293332255'
        }),
        ('bridge', {
            'UniqueId': '1757841093.31',  # 152-й канал  
            'CallerIDNum': '152',
            'ConnectedLineNum': '150',
            'BridgeUniqueid': '45ec1f12-8845-48f6-bb56-7090be11cf3a',
            'Token': '375293332255'
        }),
        
        # НОВЫЙ ВНЕШНИЙ ЗВОНОК (когда 150 и 152 уже заняты)
        ('start', {
            'UniqueId': '1757841115.33',  # НОВЫЙ ВНЕШНИЙ ЗВОНОК
            'CallType': 0,
            'Phone': '375447034448',
            'Trunk': '0001363',  # Есть Trunk = внешний звонок
            'Token': '375293332255'
        }),
        ('dial', {
            'UniqueId': '1757841115.33',
            'Extensions': ['150', '151', '152'],
            'Token': '375293332255'
        }),
        # Bridge нового внешнего звонка
        ('bridge', {
            'UniqueId': '1757841115.33',  # ОСНОВНОЙ UID нового звонка
            'CallerIDNum': '375447034448',
            'ConnectedLineNum': '151',
            'BridgeUniqueid': '012b81e0-d65b-4f4f-8e1a-7eeaf70327d4',
            'Token': '375293332255'
        }),
        # Hangup нового внешнего звонка
        ('hangup', {
            'UniqueId': '1757841115.33',  # ОСНОВНОЙ UID
            'CallStatus': '0',
            'CallType': 2,
            'Phone': '375447034448',
            'Extensions': ['152'],
            'Token': '375293332255'
        }),
        
        # ВНУТРЕННИЕ hangup (старый разговор заканчивается)
        ('hangup', {
            'UniqueId': '1757841093.31',  # Внутренний звонок 152
            'CallStatus': '2',
            'CallType': 2,
            'Phone': '152',
            'Extensions': ['150'],
            'Token': '375293332255'
        })
    ]
    
    events = create_events_from_call_sequence(call_sequence)
    
    event_filter = EventFilter()
    result = event_filter.filter_events_for_integrations(events)
    
    print(f"📊 Результат фильтрации:")
    print(f"   Сложность: {result.complexity}")
    print(f"   Основной UID: {result.primary_uid}")
    print(f"   Bitrix24: {len(result.bitrix24)} событий")
    print(f"   Telegram: {len(result.telegram)} событий")
    
    # Проверки для занятых менеджеров
    assert result.complexity == "BUSY_MANAGER", f"Ожидали BUSY_MANAGER, получили {result.complexity}"
    
    # Для занятых менеджеров должен быть показан только ПРИОРИТЕТНЫЙ звонок
    # Основной UID должен быть от внешнего звонка (приоритет)
    assert result.primary_uid == "1757841115.33", f"Неверный primary_uid: {result.primary_uid}"
    
    # Должно быть минимальное количество событий (отфильтрованы дубли)
    assert len(result.bitrix24) >= 2, f"Для BUSY_MANAGER B24 ожидали >= 2 событий, получили {len(result.bitrix24)}"
    assert len(result.telegram) >= 2, f"Для Telegram ожидали >= 2 событий, получили {len(result.telegram)}"
    
    # Проверяем что есть события для внешнего звонка
    b24_events = result.bitrix24
    has_external_start = any(e.event == 'start' and e.uniqueid == '1757841115.33' for e in b24_events)
    has_external_hangup = any(e.event == 'hangup' and e.uniqueid == '1757841115.33' for e in b24_events)
    
    assert has_external_start, "Нет start события для внешнего звонка в Bitrix24"
    assert has_external_hangup, "Нет hangup события для внешнего звонка в Bitrix24"
    
    # НЕ должно быть событий от внутреннего звонка 152↔150
    has_internal_events = any(e.uniqueid in ['1757841093.31', '1757841094.32'] for e in b24_events)
    assert not has_internal_events, "Найдены события внутреннего звонка - должны быть отфильтрованы"
    
    print("✅ Тест звонков к занятым менеджерам ПРОЙДЕН")
    return True

def test_followme_call():
    """
    Тестирует FollowMe переадресацию (тип 2-23)
    Основан на реальных данных из CallsManual_V2/2-23
    """
    print("\n🧪 Тест FollowMe переадресации (2-23)")
    
    # Упрощенная последовательность ключевых событий из 2-23
    call_sequence = [
        # ОСНОВНОЙ ВХОДЯЩИЙ ЗВОНОК
        ('start', {
            'UniqueId': '1757843259.138',  # ОСНОВНОЙ UID
            'CallType': 0,  # Входящий
            'Phone': '375447034448',
            'Trunk': '0001363',  # Есть Trunk = внешний звонок
            'Token': '375293332255'
        }),
        ('dial', {
            'UniqueId': '1757843259.138',
            'Extensions': ['150', '151', '152'],
            'Token': '375293332255'
        }),
        # Bridge основного звонка с 151
        ('bridge', {
            'UniqueId': '1757843259.138',  # ОСНОВНОЙ UID
            'CallerIDNum': '375447034448',
            'ConnectedLineNum': '151',
            'BridgeUniqueid': 'c3c5da23-07a5-4add-9583-d5e507a3ad16',
            'Token': '375293332255'
        }),
        
        # ПЕРЕАДРЕСАЦИЯ FollowMe #1 (на мобильный)
        ('start', {
            'UniqueId': '1757843283.147',  # ПЕРЕАДРЕСАЦИЯ 1
            'CallType': 1,  # Исходящий = переадресация
            'Phone': '375296254070',  # Мобильный номер
            'Trunk': '0001366',
            'Token': '375293332255'
        }),
        ('dial', {
            'UniqueId': '1757843283.147',
            'Extensions': ['151'],
            'CallType': 1,
            'Token': '375293332255'
        }),
        # Bridge переадресации
        ('bridge', {
            'UniqueId': '1757843283.147',
            'CallerIDNum': '151',
            'ConnectedLineNum': '300',
            'BridgeUniqueid': 'bb6e263b-9e67-4a71-bcff-60398d45f0db',
            'Token': '375293332255'
        }),
        
        # HANGUP переадресации (неуспешная)
        ('hangup', {
            'UniqueId': '1757843283.147',  # ПЕРЕАДРЕСАЦИЯ 1
            'CallStatus': '2',
            'CallType': 1,
            'Phone': '375296254070',
            'Extensions': ['375447034448'],
            'Token': '375293332255'
        }),
        
        # ФИНАЛЬНЫЙ HANGUP основного звонка
        ('hangup', {
            'UniqueId': '1757843259.138',  # ОСНОВНОЙ UID
            'CallStatus': '2',
            'CallType': 0,
            'Phone': '375447034448',
            'Extensions': ['151'],
            'Token': '375293332255'
        })
    ]
    
    events = create_events_from_call_sequence(call_sequence)
    
    event_filter = EventFilter()
    result = event_filter.filter_events_for_integrations(events)
    
    print(f"📊 Результат фильтрации:")
    print(f"   Сложность: {result.complexity}")
    print(f"   Основной UID: {result.primary_uid}")
    print(f"   Bitrix24: {len(result.bitrix24)} событий")
    print(f"   Telegram: {len(result.telegram)} событий")
    
    # Проверки для FollowMe
    assert result.complexity == "FOLLOWME", f"Ожидали FOLLOWME, получили {result.complexity}"
    assert result.primary_uid == "1757843259.138", f"Неверный primary_uid: {result.primary_uid}"
    
    # Для FollowMe должны быть отфильтрованы переадресации
    assert len(result.bitrix24) >= 3, f"Для FOLLOWME B24 ожидали >= 3 событий, получили {len(result.bitrix24)}"
    assert len(result.telegram) == 2, f"Для Telegram ожидали 2 события, получили {len(result.telegram)}"
    
    # Проверяем что есть события только для основного звонка
    b24_events = result.bitrix24
    has_main_start = any(e.event == 'start' and e.uniqueid == '1757843259.138' for e in b24_events)
    has_main_hangup = any(e.event == 'hangup' and e.uniqueid == '1757843259.138' for e in b24_events)
    
    assert has_main_start, "Нет start события для основного звонка в Bitrix24"
    assert has_main_hangup, "Нет hangup события для основного звонка в Bitrix24"
    
    # НЕ должно быть событий от переадресаций FollowMe
    has_redirect_events = any(e.uniqueid == '1757843283.147' for e in b24_events)
    assert not has_redirect_events, "Найдены события переадресации FollowMe - должны быть отфильтрованы"
    
    # НЕ должно быть исходящих dial событий (переадресации)
    has_outgoing_dial = any(e.event == 'dial' and e.data.get('CallType') == 1 for e in b24_events)
    assert not has_outgoing_dial, "Найдены исходящие dial события - должны быть отфильтрованы"
    
    print("✅ Тест FollowMe переадресации ПРОЙДЕН")
    return True

def main():
    """Запуск всех тестов"""
    print("🚀 Запуск тестов системы фильтрации событий")
    print("=" * 60)
    
    try:
        test_simple_incoming_call()
        test_simple_outgoing_call() 
        test_complex_call_detection()
        test_multiple_transfer_call()
        test_busy_manager_call()
        test_followme_call()  # НОВЫЙ ТЕСТ
        
        print("\n" + "=" * 60)
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("✅ Базовая фильтрация событий работает корректно")
        print("✅ Множественные переводы обрабатываются правильно")
        print("✅ Занятые менеджеры обрабатываются правильно")
        print("✅ FollowMe переадресации обрабатываются правильно")
        print("✅ Система готова к production deployment!")
        
    except AssertionError as e:
        print(f"\n❌ ТЕСТ ПРОВАЛЕН: {e}")
        return False
    except Exception as e:
        print(f"\n💥 ОШИБКА В ТЕСТАХ: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
