#!/usr/bin/env python3
"""
Система кэширования и фильтрации событий звонков

Автор: Assistant
Дата: 2025-09-14
"""

import json
import redis
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import asdict

from .event_filter import EventFilter, Event, FilterResult


class EventCache:
    """
    Система кэширования событий с умной фильтрацией
    
    Архитектура:
    - Сырые события: events:{call_id} 
    - Фильтрованные: filtered:{call_id}:{integration}
    - Метаданные: metadata:{call_id}
    - Статус: status:{call_id}
    """
    
    def __init__(self, redis_host='localhost', redis_port=6379, redis_db=0):
        self.redis = redis.Redis(host=redis_host, port=redis_port, db=redis_db)
        self.event_filter = EventFilter()
        self.logger = logging.getLogger(__name__)
        
        # Настройки TTL (время жизни в кэше)
        self.ACTIVE_CALL_TTL = 3600 * 4    # 4 часа для активных звонков
        self.COMPLETED_CALL_TTL = 3600 * 24 * 7  # 7 дней для завершенных
        
    # ═══════════════════════════════════════════════════════════════════
    # ОСНОВНЫЕ МЕТОДЫ РАБОТЫ С СОБЫТИЯМИ
    # ═══════════════════════════════════════════════════════════════════
    
    def add_event(self, event: Event) -> None:
        """
        Добавляет событие в кэш и триггерит пересчет фильтрации
        """
        call_id = event.uniqueid
        
        # Сохраняем сырое событие
        event_data = {
            'event': event.event,
            'uniqueid': event.uniqueid,
            'timestamp': event.timestamp.isoformat(),
            'data': event.data
        }
        
        self.redis.lpush(f"events:{call_id}", json.dumps(event_data))
        self.redis.expire(f"events:{call_id}", self.ACTIVE_CALL_TTL)
        
        # Обновляем статус звонка
        self._update_call_status(call_id, event)
        
        # Инвалидируем кэш фильтрации (пересчитаем при запросе)
        self._invalidate_filter_cache(call_id)
        
        self.logger.debug(f"💾 Добавлено событие {event.event} для звонка {call_id}")
    
    def get_filtered_events(self, call_id: str, integration: str) -> List[Dict]:
        """
        Получает отфильтрованные события для конкретной интеграции
        
        Args:
            call_id: Идентификатор звонка
            integration: Тип интеграции (bitrix24, telegram, general)
        """
        cache_key = f"filtered:{call_id}:{integration}"
        
        # Проверяем кэш
        cached_result = self.redis.get(cache_key)
        if cached_result:
            self.logger.debug(f"🎯 Кэш попадание для {call_id}:{integration}")
            return json.loads(cached_result)
        
        # Если нет в кэше - вычисляем
        self.logger.debug(f"🔄 Кэш промах для {call_id}:{integration}, вычисляем...")
        return self._compute_and_cache_filter(call_id, integration)
    
    def get_call_metadata(self, call_id: str) -> Optional[Dict]:
        """
        Получает метаданные звонка (сложность, основной UID, статус)
        """
        metadata = self.redis.hgetall(f"metadata:{call_id}")
        if metadata:
            # Redis возвращает bytes, конвертируем в строки
            return {k.decode(): v.decode() for k, v in metadata.items()}
        return None
    
    # ═══════════════════════════════════════════════════════════════════
    # ВНУТРЕННИЕ МЕТОДЫ
    # ═══════════════════════════════════════════════════════════════════
    
    def _compute_and_cache_filter(self, call_id: str, integration: str) -> List[Dict]:
        """
        Вычисляет фильтрацию и кэширует результат
        """
        # Загружаем сырые события
        raw_events = self._load_raw_events(call_id)
        if not raw_events:
            return []
        
        # Применяем фильтрацию
        filter_result = self.event_filter.filter_events_for_integrations(raw_events)
        
        # Сохраняем метаданные
        self._save_call_metadata(call_id, filter_result)
        
        # Кэшируем результаты для всех интеграций
        self._cache_all_integrations(call_id, filter_result)
        
        # Возвращаем запрошенную интеграцию
        return self._get_integration_events(filter_result, integration)
    
    def _load_raw_events(self, call_id: str) -> List[Event]:
        """
        Загружает сырые события из кэша и преобразует в объекты Event
        """
        raw_data = self.redis.lrange(f"events:{call_id}", 0, -1)
        events = []
        
        for item in reversed(raw_data):  # Восстанавливаем порядок
            try:
                event_data = json.loads(item)
                event = Event(
                    event=event_data['event'],
                    uniqueid=event_data['uniqueid'],
                    timestamp=datetime.fromisoformat(event_data['timestamp']),
                    data=event_data['data']
                )
                events.append(event)
            except (json.JSONDecodeError, KeyError) as e:
                self.logger.error(f"❌ Ошибка парсинга события: {e}")
        
        return events
    
    def _save_call_metadata(self, call_id: str, filter_result: FilterResult) -> None:
        """
        Сохраняет метаданные звонка в кэш
        """
        metadata = {
            'complexity': filter_result.complexity,
            'primary_uid': filter_result.primary_uid,
            'total_events': len(filter_result.general),
            'bitrix24_events': len(filter_result.bitrix24),
            'telegram_events': len(filter_result.telegram),
            'last_updated': datetime.now().isoformat()
        }
        
        self.redis.hset(f"metadata:{call_id}", mapping=metadata)
        self.redis.expire(f"metadata:{call_id}", self.COMPLETED_CALL_TTL)
    
    def _cache_all_integrations(self, call_id: str, filter_result: FilterResult) -> None:
        """
        Кэширует результаты фильтрации для всех интеграций
        """
        ttl = self._get_cache_ttl(call_id)
        
        integrations = {
            'bitrix24': filter_result.bitrix24,
            'telegram': filter_result.telegram,
            'general': filter_result.general
        }
        
        for integration, events in integrations.items():
            cache_key = f"filtered:{call_id}:{integration}"
            event_dicts = [self._event_to_dict(e) for e in events]
            
            self.redis.setex(cache_key, ttl, json.dumps(event_dicts))
        
        self.logger.debug(f"💾 Закэширована фильтрация для {call_id} ({filter_result.complexity})")
    
    def _get_integration_events(self, filter_result: FilterResult, integration: str) -> List[Dict]:
        """
        Получает события для конкретной интеграции из FilterResult
        """
        events = getattr(filter_result, integration, filter_result.general)
        return [self._event_to_dict(e) for e in events]
    
    def _event_to_dict(self, event: Event) -> Dict:
        """
        Преобразует Event в словарь для JSON сериализации
        """
        return {
            'event': event.event,
            'uniqueid': event.uniqueid,
            'timestamp': event.timestamp.isoformat(),
            'data': event.data
        }
    
    def _update_call_status(self, call_id: str, event: Event) -> None:
        """
        Обновляет статус звонка на основе событий
        """
        if event.event == 'start':
            self.redis.hset(f"status:{call_id}", "status", "active")
            self.redis.hset(f"status:{call_id}", "started_at", event.timestamp.isoformat())
        elif event.event == 'hangup':
            self.redis.hset(f"status:{call_id}", "status", "completed") 
            self.redis.hset(f"status:{call_id}", "ended_at", event.timestamp.isoformat())
            
        self.redis.expire(f"status:{call_id}", self.COMPLETED_CALL_TTL)
    
    def _invalidate_filter_cache(self, call_id: str) -> None:
        """
        Инвалидирует кэш фильтрации для звонка
        """
        patterns = [
            f"filtered:{call_id}:*"
        ]
        
        for pattern in patterns:
            keys = self.redis.keys(pattern)
            if keys:
                self.redis.delete(*keys)
    
    def _get_cache_ttl(self, call_id: str) -> int:
        """
        Определяет TTL для кэша на основе статуса звонка
        """
        status = self.redis.hget(f"status:{call_id}", "status")
        if status and status.decode() == "completed":
            return self.COMPLETED_CALL_TTL
        return self.ACTIVE_CALL_TTL
    
    # ═══════════════════════════════════════════════════════════════════
    # УТИЛИТЫ И ДИАГНОСТИКА
    # ═══════════════════════════════════════════════════════════════════
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Получает статистику кэша
        """
        # Подсчитываем ключи по типам
        events_keys = len(self.redis.keys("events:*"))
        filtered_keys = len(self.redis.keys("filtered:*"))
        metadata_keys = len(self.redis.keys("metadata:*"))
        status_keys = len(self.redis.keys("status:*"))
        
        return {
            'total_calls': events_keys,
            'cached_filters': filtered_keys,
            'metadata_entries': metadata_keys,
            'status_entries': status_keys,
            'redis_memory_usage': self.redis.info()['used_memory_human']
        }
    
    def cleanup_old_calls(self, days_old: int = 7) -> int:
        """
        Очищает старые завершенные звонки
        """
        cutoff_date = datetime.now() - timedelta(days=days_old)
        cleaned = 0
        
        # Получаем все статусы
        for key in self.redis.keys("status:*"):
            call_id = key.decode().split(":", 1)[1]
            ended_at = self.redis.hget(key, "ended_at")
            
            if ended_at:
                try:
                    end_time = datetime.fromisoformat(ended_at.decode())
                    if end_time < cutoff_date:
                        self._delete_call_data(call_id)
                        cleaned += 1
                except ValueError:
                    continue
        
        return cleaned
    
    def _delete_call_data(self, call_id: str) -> None:
        """
        Удаляет все данные звонка из кэша
        """
        patterns = [
            f"events:{call_id}",
            f"filtered:{call_id}:*", 
            f"metadata:{call_id}",
            f"status:{call_id}"
        ]
        
        keys_to_delete = []
        for pattern in patterns:
            if '*' in pattern:
                keys_to_delete.extend(self.redis.keys(pattern))
            else:
                keys_to_delete.append(pattern)
        
        if keys_to_delete:
            self.redis.delete(*keys_to_delete)
