#!/usr/bin/env python3
"""
Пример интеграции EventCache с bridge.py

Показывает как кэшированная фильтрация может работать в реальной системе
"""

import json
import asyncio
from datetime import datetime
from app.services.calls.event_cache import EventCache
from app.services.calls.event_filter import Event


class CachedWebhookHandler:
    """
    Пример обработчика webhook с кэшированной фильтрацией
    """
    
    def __init__(self):
        self.event_cache = EventCache()
    
    async def handle_ami_event(self, ami_data: dict):
        """
        Обрабатывает входящее AMI событие
        """
        # Преобразуем AMI данные в Event объект
        event = Event(
            event=ami_data.get('Event', '').lower(),
            uniqueid=ami_data.get('Uniqueid', ''),
            timestamp=datetime.now(),
            data=ami_data
        )
        
        # Добавляем в кэш (автоматически инвалидирует фильтры)
        self.event_cache.add_event(event)
        
        print(f"📨 Обработано событие {event.event} для звонка {event.uniqueid}")
    
    async def send_to_bitrix24(self, call_id: str):
        """
        Отправляет отфильтрованные события в Bitrix24
        """
        # Получаем отфильтрованные события из кэша
        events = self.event_cache.get_filtered_events(call_id, 'bitrix24')
        metadata = self.event_cache.get_call_metadata(call_id)
        
        if not events:
            return
        
        print(f"📤 Отправка в Bitrix24 для звонка {call_id}:")
        print(f"   Сложность: {metadata.get('complexity', 'UNKNOWN')}")
        print(f"   События: {len(events)}")
        
        for event in events:
            # Тут была бы реальная отправка в Bitrix24
            print(f"   → {event['event']} ({event['uniqueid']})")
    
    async def send_to_telegram(self, call_id: str):
        """
        Отправляет уведомления в Telegram
        """
        events = self.event_cache.get_filtered_events(call_id, 'telegram')
        metadata = self.event_cache.get_call_metadata(call_id)
        
        if not events:
            return
        
        print(f"📱 Отправка в Telegram для звонка {call_id}:")
        print(f"   Сложность: {metadata.get('complexity', 'UNKNOWN')}")
        print(f"   События: {len(events)}")
        
        # Telegram обычно получает только start/hangup
        for event in events:
            print(f"   → {event['event']} ({event['uniqueid']})")


async def demo_cached_filtering():
    """
    Демонстрация работы кэшированной фильтрации
    """
    handler = CachedWebhookHandler()
    
    print("🚀 Демонстрация кэшированной фильтрации событий")
    print("=" * 60)
    
    # Симулируем входящий звонок с FollowMe переадресацией
    call_id = "1757843259.138"
    
    # Последовательность событий (упрощенная версия 2-23)
    ami_events = [
        {
            'Event': 'Start',
            'Uniqueid': call_id,
            'CallType': 0,
            'Phone': '375447034448',
            'Trunk': '0001363'
        },
        {
            'Event': 'Dial', 
            'Uniqueid': call_id,
            'Extensions': ['150', '151', '152']
        },
        {
            'Event': 'Bridge',
            'Uniqueid': call_id,
            'CallerIDNum': '375447034448',
            'ConnectedLineNum': '151',
            'BridgeUniqueid': 'main-bridge-123'
        },
        # FollowMe переадресация (будет отфильтрована)
        {
            'Event': 'Start',
            'Uniqueid': '1757843283.147',  # Переадресация
            'CallType': 1,  # Исходящий
            'Phone': '375296254070'
        },
        {
            'Event': 'Hangup',
            'Uniqueid': '1757843283.147',  # Переадресация
            'CallStatus': '2'
        },
        # Финальный hangup основного звонка
        {
            'Event': 'Hangup',
            'Uniqueid': call_id,
            'CallStatus': '2',
            'Phone': '375447034448'
        }
    ]
    
    # Обрабатываем события
    print("\n📨 Обработка входящих AMI событий:")
    for ami_event in ami_events:
        await handler.handle_ami_event(ami_event)
        await asyncio.sleep(0.1)  # Имитируем реальные интервалы
    
    print("\n" + "=" * 60)
    
    # Отправляем в интеграции
    print("\n📤 Отправка в интеграции:")
    await handler.send_to_bitrix24(call_id)
    print()
    await handler.send_to_telegram(call_id)
    
    # Показываем статистику кэша
    print("\n" + "=" * 60)
    print("\n📊 Статистика кэша:")
    stats = handler.event_cache.get_cache_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")


if __name__ == "__main__":
    # Запускаем демонстрацию
    asyncio.run(demo_cached_filtering())
