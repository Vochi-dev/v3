#!/usr/bin/env python3
"""
Умный менеджер кэша с гибридной архитектурой

Автор: Assistant  
Дата: 2025-09-14
"""

import asyncio
import json
import logging
from typing import Set, Dict, Optional
from datetime import datetime, timedelta

from .event_cache import EventCache
from .event_filter import Event


class SmartCacheManager:
    """
    Умный менеджер кэша с несколькими стратегиями:
    
    1. Reactive - фильтрация по требованию (для активных звонков)
    2. Proactive - предварительная фильтрация (для завершенных звонков) 
    3. Smart triggers - триггеры для оптимальной обработки
    """
    
    def __init__(self, event_cache: Optional[EventCache] = None):
        self.event_cache = event_cache or EventCache()
        self.logger = logging.getLogger(__name__)
        
        # Настройки
        self.FILTER_DELAY = 2  # Задержка перед фильтрацией активных звонков (сек)
        self.BATCH_SIZE = 10   # Размер batch для обработки
        
        # Состояние
        self.pending_calls: Set[str] = set()  # Звонки ожидающие фильтрации
        self.processing_calls: Set[str] = set()  # Звонки в процессе фильтрации
        self.background_task: Optional[asyncio.Task] = None
    
    # ═══════════════════════════════════════════════════════════════════
    # ОСНОВНОЙ API
    # ═══════════════════════════════════════════════════════════════════
    
    async def handle_new_event(self, event: Event) -> None:
        """
        Обрабатывает новое событие AMI
        
        Стратегия:
        1. Сохраняем событие в кэш
        2. Определяем нужна ли немедленная фильтрация
        3. Планируем фильтрацию или ставим в очередь
        """
        call_id = event.uniqueid
        
        # Сохраняем событие
        self.event_cache.add_event(event)
        
        # Определяем стратегию обработки
        strategy = self._determine_processing_strategy(event, call_id)
        
        if strategy == "immediate":
            # Немедленная фильтрация (для hangup событий)
            await self._process_call_immediately(call_id)
            
        elif strategy == "delayed":
            # Отложенная фильтрация (для активных звонков)
            self._schedule_delayed_processing(call_id)
            
        elif strategy == "background":
            # Фоновая обработка (для сложных звонков)
            self._add_to_background_queue(call_id)
    
    async def get_filtered_events(self, call_id: str, integration: str) -> list:
        """
        Получает отфильтрованные события с умной стратегией
        """
        # Проверяем есть ли в кэше
        cached_events = self.event_cache.get_filtered_events(call_id, integration)
        if cached_events:
            return cached_events
        
        # Если нет в кэше - проверяем статус обработки
        if call_id in self.processing_calls:
            # Звонок обрабатывается - ждем
            await self._wait_for_processing(call_id)
            return self.event_cache.get_filtered_events(call_id, integration)
        
        # Если звонок не обрабатывается - запускаем немедленную фильтрацию
        await self._process_call_immediately(call_id)
        return self.event_cache.get_filtered_events(call_id, integration)
    
    async def start_background_processing(self) -> None:
        """
        Запускает фоновую обработку очереди
        """
        if self.background_task and not self.background_task.done():
            return
        
        self.background_task = asyncio.create_task(self._background_processor())
        self.logger.info("🚀 Запущена фоновая обработка кэша")
    
    async def stop_background_processing(self) -> None:
        """
        Останавливает фоновую обработку
        """
        if self.background_task:
            self.background_task.cancel()
            try:
                await self.background_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("⏹️ Остановлена фоновая обработка кэша")
    
    # ═══════════════════════════════════════════════════════════════════
    # СТРАТЕГИИ ОБРАБОТКИ
    # ═══════════════════════════════════════════════════════════════════
    
    def _determine_processing_strategy(self, event: Event, call_id: str) -> str:
        """
        Определяет стратегию обработки на основе типа события
        """
        # Немедленная обработка для завершающих событий
        if event.event == 'hangup':
            return "immediate"
        
        # Проверяем метаданные звонка
        metadata = self.event_cache.get_call_metadata(call_id)
        if metadata:
            complexity = metadata.get('complexity', 'UNKNOWN')
            
            # Сложные звонки - в фоновую обработку
            if complexity in ['FOLLOWME', 'MULTIPLE_TRANSFER']:
                return "background"
        
        # Для остальных - отложенная обработка
        return "delayed"
    
    async def _process_call_immediately(self, call_id: str) -> None:
        """
        Немедленная фильтрация звонка
        """
        if call_id in self.processing_calls:
            return
        
        self.processing_calls.add(call_id)
        try:
            # Принудительно вычисляем фильтрацию для всех интеграций
            for integration in ['bitrix24', 'telegram', 'general']:
                self.event_cache.get_filtered_events(call_id, integration)
            
            self.logger.debug(f"⚡ Немедленно обработан звонок {call_id}")
            
        finally:
            self.processing_calls.discard(call_id)
            self.pending_calls.discard(call_id)
    
    def _schedule_delayed_processing(self, call_id: str) -> None:
        """
        Планирует отложенную обработку звонка
        """
        self.pending_calls.add(call_id)
        
        # Создаем задачу с задержкой
        async def delayed_process():
            await asyncio.sleep(self.FILTER_DELAY)
            if call_id in self.pending_calls:
                await self._process_call_immediately(call_id)
        
        asyncio.create_task(delayed_process())
    
    def _add_to_background_queue(self, call_id: str) -> None:
        """
        Добавляет звонок в очередь фоновой обработки
        """
        self.pending_calls.add(call_id)
    
    async def _wait_for_processing(self, call_id: str, max_wait: float = 5.0) -> None:
        """
        Ждет завершения обработки звонка
        """
        start_time = asyncio.get_event_loop().time()
        
        while call_id in self.processing_calls:
            if asyncio.get_event_loop().time() - start_time > max_wait:
                self.logger.warning(f"⏰ Таймаут ожидания обработки {call_id}")
                break
            
            await asyncio.sleep(0.1)
    
    # ═══════════════════════════════════════════════════════════════════
    # ФОНОВАЯ ОБРАБОТКА
    # ═══════════════════════════════════════════════════════════════════
    
    async def _background_processor(self) -> None:
        """
        Фоновый процессор очереди звонков
        """
        while True:
            try:
                # Получаем batch звонков для обработки
                calls_to_process = list(self.pending_calls)[:self.BATCH_SIZE]
                
                if calls_to_process:
                    await self._process_batch(calls_to_process)
                
                # Ждем перед следующей итерацией
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Ошибка в фоновом процессоре: {e}")
                await asyncio.sleep(5)  # Пауза при ошибке
    
    async def _process_batch(self, call_ids: list) -> None:
        """
        Обрабатывает batch звонков
        """
        for call_id in call_ids:
            if call_id not in self.pending_calls:
                continue
            
            try:
                await self._process_call_immediately(call_id)
                
            except Exception as e:
                self.logger.error(f"❌ Ошибка обработки {call_id}: {e}")
        
        self.logger.debug(f"📦 Обработан batch из {len(call_ids)} звонков")
    
    # ═══════════════════════════════════════════════════════════════════
    # МОНИТОРИНГ И ДИАГНОСТИКА
    # ═══════════════════════════════════════════════════════════════════
    
    def get_processing_stats(self) -> Dict:
        """
        Получает статистику обработки
        """
        return {
            'pending_calls': len(self.pending_calls),
            'processing_calls': len(self.processing_calls),
            'background_task_running': self.background_task and not self.background_task.done(),
            'cache_stats': self.event_cache.get_cache_stats()
        }
    
    async def health_check(self) -> Dict:
        """
        Проверка здоровья системы
        """
        stats = self.get_processing_stats()
        
        # Проверяем нет ли застрявших звонков
        stuck_calls = len(self.processing_calls) > 0
        queue_overload = len(self.pending_calls) > 100
        
        return {
            'status': 'healthy' if not (stuck_calls or queue_overload) else 'warning',
            'issues': {
                'stuck_calls': stuck_calls,
                'queue_overload': queue_overload
            },
            'stats': stats
        }
