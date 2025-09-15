#!/usr/bin/env python3
"""
Интеграция SmartCacheManager с bridge.py

Показывает как подключить умную кэшированную фильтрацию
к существующей системе обработки AMI событий
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any

from app.services.calls.smart_cache_manager import SmartCacheManager
from app.services.calls.event_filter import Event


class EnhancedWebhookHandler:
    """
    Расширенный обработчик webhook с умным кэшированием
    """
    
    def __init__(self):
        self.cache_manager = SmartCacheManager()
        self.logger = logging.getLogger(__name__)
        
        # Запускаем фоновую обработку
        asyncio.create_task(self.cache_manager.start_background_processing())
    
    # ═══════════════════════════════════════════════════════════════════
    # ОБРАБОТКА AMI СОБЫТИЙ (интеграция с bridge.py)
    # ═══════════════════════════════════════════════════════════════════
    
    async def handle_ami_event(self, ami_data: Dict[str, Any]) -> None:
        """
        Главный метод для интеграции с bridge.py
        
        Заменяет логику в bridge.py для обработки событий
        """
        try:
            # Преобразуем AMI данные в Event объект
            event = self._ami_to_event(ami_data)
            
            if event:
                # Передаем событие в умный кэш-менеджер
                await self.cache_manager.handle_new_event(event)
                
                # Логируем для мониторинга
                self.logger.debug(
                    f"📨 AMI событие {event.event} для звонка {event.uniqueid} обработано"
                )
        
        except Exception as e:
            self.logger.error(f"❌ Ошибка обработки AMI события: {e}")
    
    def _ami_to_event(self, ami_data: Dict[str, Any]) -> Event:
        """
        Преобразует AMI данные в объект Event
        """
        event_type = ami_data.get('Event', '').lower()
        uniqueid = ami_data.get('Uniqueid', '')
        
        if not event_type or not uniqueid:
            return None
        
        return Event(
            event=event_type,
            uniqueid=uniqueid,
            timestamp=datetime.now(),
            data=ami_data
        )
    
    # ═══════════════════════════════════════════════════════════════════
    # API ДЛЯ ИНТЕГРАЦИЙ (замена старых методов)
    # ═══════════════════════════════════════════════════════════════════
    
    async def get_events_for_bitrix24(self, call_id: str) -> list:
        """
        API для получения событий для Bitrix24
        """
        return await self.cache_manager.get_filtered_events(call_id, 'bitrix24')
    
    async def get_events_for_telegram(self, call_id: str) -> list:
        """
        API для получения событий для Telegram
        """
        return await self.cache_manager.get_filtered_events(call_id, 'telegram')
    
    async def get_events_for_webhook(self, call_id: str) -> list:
        """
        API для получения событий для внешних webhook
        """
        return await self.cache_manager.get_filtered_events(call_id, 'general')
    
    async def get_call_metadata(self, call_id: str) -> Dict:
        """
        Получает метаданные звонка (сложность, статус, etc.)
        """
        return self.cache_manager.event_cache.get_call_metadata(call_id) or {}
    
    # ═══════════════════════════════════════════════════════════════════
    # ОТПРАВКА В ИНТЕГРАЦИИ (замена существующих методов)
    # ═══════════════════════════════════════════════════════════════════
    
    async def send_to_bitrix24(self, call_id: str) -> bool:
        """
        Отправляет отфильтрованные события в Bitrix24
        """
        try:
            events = await self.get_events_for_bitrix24(call_id)
            metadata = await self.get_call_metadata(call_id)
            
            if not events:
                return False
            
            # Тут была бы реальная отправка в Bitrix24 API
            self._log_integration_send('Bitrix24', call_id, events, metadata)
            
            # Имитация отправки
            for event in events:
                await self._send_bitrix24_event(event, metadata)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка отправки в Bitrix24 для {call_id}: {e}")
            return False
    
    async def send_to_telegram(self, call_id: str) -> bool:
        """
        Отправляет уведомления в Telegram
        """
        try:
            events = await self.get_events_for_telegram(call_id)
            metadata = await self.get_call_metadata(call_id)
            
            if not events:
                return False
            
            # Тут была бы реальная отправка в Telegram Bot API
            self._log_integration_send('Telegram', call_id, events, metadata)
            
            # Telegram обычно получает только start/hangup
            for event in events:
                await self._send_telegram_notification(event, metadata)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка отправки в Telegram для {call_id}: {e}")
            return False
    
    async def send_to_webhook(self, call_id: str, webhook_url: str) -> bool:
        """
        Отправляет события на внешний webhook
        """
        try:
            events = await self.get_events_for_webhook(call_id)
            metadata = await self.get_call_metadata(call_id)
            
            if not events:
                return False
            
            # Тут была бы реальная HTTP POST отправка
            self._log_integration_send('Webhook', call_id, events, metadata)
            
            payload = {
                'call_id': call_id,
                'metadata': metadata,
                'events': events
            }
            
            # await self._http_post(webhook_url, payload)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка отправки webhook для {call_id}: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ═══════════════════════════════════════════════════════════════════
    
    def _log_integration_send(self, integration: str, call_id: str, events: list, metadata: dict):
        """
        Логирует отправку в интеграцию
        """
        complexity = metadata.get('complexity', 'UNKNOWN')
        self.logger.info(
            f"📤 {integration}: {call_id} ({complexity}) → {len(events)} событий"
        )
    
    async def _send_bitrix24_event(self, event: dict, metadata: dict):
        """
        Имитация отправки события в Bitrix24
        """
        # Здесь был бы реальный API вызов Bitrix24
        event_type = event['event']
        if event_type == 'start':
            # telephony.externalcall.register
            pass
        elif event_type == 'bridge':
            # telephony.externalcall.hide
            pass
        elif event_type == 'hangup':
            # telephony.externalcall.finish
            pass
    
    async def _send_telegram_notification(self, event: dict, metadata: dict):
        """
        Имитация отправки уведомления в Telegram
        """
        # Здесь был бы реальный Bot API вызов
        event_type = event['event']
        if event_type == 'start':
            # Уведомление о входящем звонке
            pass
        elif event_type == 'hangup':
            # Уведомление о завершении звонка
            pass
    
    # ═══════════════════════════════════════════════════════════════════
    # МОНИТОРИНГ И ДИАГНОСТИКА
    # ═══════════════════════════════════════════════════════════════════
    
    async def get_system_health(self) -> Dict:
        """
        Проверка здоровья системы
        """
        return await self.cache_manager.health_check()
    
    def get_system_stats(self) -> Dict:
        """
        Статистика работы системы
        """
        return self.cache_manager.get_processing_stats()


# ═══════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ С СУЩЕСТВУЮЩИМ bridge.py
# ═══════════════════════════════════════════════════════════════════

# Глобальный экземпляр обработчика
webhook_handler = EnhancedWebhookHandler()

def integrate_with_bridge():
    """
    Функция для интеграции с существующим bridge.py
    
    Заменяет методы в bridge.py на новые с кэшированием
    """
    
    # В bridge.py заменить:
    # 
    # def handle_ami_event(self, ami_data):
    #     # старая логика
    # 
    # на:
    # 
    # async def handle_ami_event(self, ami_data):
    #     await webhook_handler.handle_ami_event(ami_data)
    #
    # def send_to_bitrix24(self, call_data):
    #     # старая логика
    #
    # на:
    #
    # async def send_to_bitrix24(self, call_id):
    #     return await webhook_handler.send_to_bitrix24(call_id)
    
    pass


if __name__ == "__main__":
    # Пример использования
    async def demo():
        handler = EnhancedWebhookHandler()
        
        # Имитируем AMI события
        ami_events = [
            {'Event': 'Start', 'Uniqueid': '123', 'Phone': '375447034448'},
            {'Event': 'Bridge', 'Uniqueid': '123', 'CallerIDNum': '375447034448'},
            {'Event': 'Hangup', 'Uniqueid': '123', 'CallStatus': '2'}
        ]
        
        # Обрабатываем события
        for ami_event in ami_events:
            await handler.handle_ami_event(ami_event)
        
        # Отправляем в интеграции
        await handler.send_to_bitrix24('123')
        await handler.send_to_telegram('123')
        
        # Проверяем здоровье системы
        health = await handler.get_system_health()
        print("Здоровье системы:", health)
    
    asyncio.run(demo())
