"""
Система фильтрации событий для интеграций (Bitrix24, Telegram, CRM)

Основано на анализе 42 типов звонков из events.md.
Реализует умную фильтрацию bridge событий для различных интеграций.
"""

import logging
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════
# СТРУКТУРЫ ДАННЫХ ДЛЯ СОБЫТИЙ
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Event:
    """Структура события Asterisk"""
    event: str              # Тип события: start, dial, bridge, hangup, etc.
    uniqueid: str           # UniqueId звонка
    timestamp: datetime     # Время события
    data: Dict[str, Any]    # Полные данные события
    
    def get_related_uids(self) -> List[str]:
        """Возвращает все связанные UniqueId для этого события"""
        uids = [self.uniqueid] if self.uniqueid else []
        
        # Для bridge событий добавляем связанные UID
        if self.event in ['bridge', 'bridge_leave']:
            # В bridge событии может быть связь через CallerIDNum и ConnectedLineNum
            caller = self.data.get('CallerIDNum', '')
            connected = self.data.get('ConnectedLineNum', '')
            
            # Если в событии есть другие номера, они могут быть связаны
            # с основным звонком через bridge
            if caller and caller != self.uniqueid:
                # Номер может быть внешним номером основного звонка
                pass
            if connected and connected != self.uniqueid:
                # Номер может быть внутренним номером
                pass
                
        return uids

@dataclass
class FilterResult:
    """Результат фильтрации событий"""
    bitrix24: List[Event]   # События для Bitrix24
    telegram: List[Event]   # События для Telegram  
    crm: List[Event]       # События для CRM (все)
    general: List[Event]   # Общие события
    complexity: str        # Тип сложности звонка
    primary_uid: str       # Основной UniqueId

# ═══════════════════════════════════════════════════════════════════
# ОСНОВНОЙ КЛАСС ФИЛЬТРАЦИИ
# ═══════════════════════════════════════════════════════════════════

class EventFilter:
    """
    Главный класс для фильтрации событий Asterisk
    
    Анализирует последовательности событий и выделяет только значимые
    для каждой интеграции на основе типа сложности звонка.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.info("🔧 EventFilter инициализирован")
    
    # ───────────────────────────────────────────────────────────────────
    # ГЛАВНАЯ ФУНКЦИЯ ФИЛЬТРАЦИИ
    # ───────────────────────────────────────────────────────────────────
    
    def filter_events_for_integrations(self, events: List[Event]) -> FilterResult:
        """
        Главная функция фильтрации событий для интеграций
        
        Args:
            events: Список событий одного звонка
            
        Returns:
            FilterResult с отфильтрованными событиями для каждой интеграции
        """
        self.logger.info(f"🎯 Начинаем фильтрацию {len(events)} событий")
        
        if not events:
            return FilterResult([], [], [], [], "EMPTY", "")
        
        # Определяем основной UniqueId и сложность
        primary_uid = self.get_primary_uniqueid(events)
        complexity = self.get_call_complexity(events)
        
        self.logger.info(f"📋 Основной UID: {primary_uid}, Сложность: {complexity}")
        
        # Фильтруем согласно сложности
        if complexity == "FOLLOWME":
            filtered = self._filter_followme_events(events, primary_uid)
        elif complexity == "MULTIPLE_TRANSFER":
            filtered = self._filter_multiple_transfer_events(events, primary_uid)
        elif complexity == "BUSY_MANAGER":
            filtered = self._filter_busy_manager_events(events)
        else:
            filtered = self._filter_simple_events(events, primary_uid)
        
        result = FilterResult(
            bitrix24=filtered.get('bitrix24', []),
            telegram=filtered.get('telegram', []),
            crm=events,  # CRM получает все события
            general=filtered.get('general', []),
            complexity=complexity,
            primary_uid=primary_uid
        )
        
        self.logger.info(f"✅ Фильтрация завершена: B24={len(result.bitrix24)}, TG={len(result.telegram)}, CRM={len(result.crm)}")
        
        return result
    
    # ───────────────────────────────────────────────────────────────────
    # ОПРЕДЕЛЕНИЕ ОСНОВНОГО UNIQUEID
    # ───────────────────────────────────────────────────────────────────
    
    def get_primary_uniqueid(self, events: List[Event]) -> str:
        """
        Определяет основной UniqueId звонка
        
        Логика:
        - Для входящих: ищем start событие
        - Для исходящих: ищем первый dial
        - Для внутренних: первый bridge
        """
        if not events:
            return ""
        
        # Для входящих звонков - ищем start
        for event in events:
            if event.event == 'start':
                self.logger.debug(f"🔍 Найден start event с UID: {event.uniqueid}")
                return event.uniqueid
        
        # Для исходящих звонков - ищем первый dial
        for event in events:
            if event.event == 'dial':
                self.logger.debug(f"🔍 Найден dial event с UID: {event.uniqueid}")
                return event.uniqueid
        
        # Для внутренних или неопознанных - первый доступный UID
        first_event = events[0]
        self.logger.debug(f"🔍 Используем первый доступный UID: {first_event.uniqueid}")
        return first_event.uniqueid
    
    # ───────────────────────────────────────────────────────────────────
    # ОПРЕДЕЛЕНИЕ ТИПА СЛОЖНОСТИ ЗВОНКА
    # ───────────────────────────────────────────────────────────────────
    
    def get_call_complexity(self, events: List[Event]) -> str:
        """
        Определяет тип сложности звонка на основе анализа событий
        
        Типы сложности:
        - FOLLOWME: FollowMe переадресация (2-23-2-30) - 35+ событий
        - MULTIPLE_TRANSFER: Множественный перевод (2-18) - 5+ мостов  
        - BUSY_MANAGER: Звонок занятому менеджеру (2-19-2-22) - множественные start
        - COMPLEX_TRANSFER: Сложная переадресация - 3+ мостов
        - SIMPLE: Простой звонок
        """
        if not events:
            return "EMPTY"
        
        # Подсчитываем ключевые метрики
        total_events = len(events)
        bridges = len([e for e in events if e.event == 'bridge'])
        bridge_creates = len([e for e in events if e.event == 'bridge_create'])
        starts = len([e for e in events if e.event == 'start'])
        
        self.logger.debug(f"📊 Метрики: события={total_events}, мосты={bridges}, создания={bridge_creates}, старты={starts}")
        
        # FollowMe - самые сложные сценарии
        if total_events > 35:
            self.logger.info("🌊 Определен тип: FOLLOWME (>35 событий)")
            return "FOLLOWME"
        
        # Множественный перевод - много мостов
        if bridges > 4:
            self.logger.info("⚡ Определен тип: MULTIPLE_TRANSFER (>4 мостов)")
            return "MULTIPLE_TRANSFER"
        
        # Сложная переадресация - несколько мостов
        if bridge_creates > 2:
            self.logger.info("🔄 Определен тип: COMPLEX_TRANSFER (>2 создания мостов)")
            return "COMPLEX_TRANSFER"
        
        # Проверка FollowMe: множественные start с CallType=1 (исходящие переадресации)
        start_events = [e for e in events if e.event == 'start']
        outgoing_starts = [e for e in start_events if e.data.get('CallType') == 1]
        incoming_starts = [e for e in start_events if e.data.get('CallType') == 0 and e.data.get('Trunk')]
        
        if len(incoming_starts) >= 1 and len(outgoing_starts) >= 1:
            self.logger.info("🌊 Определен тип: FOLLOWME (входящий + исходящие переадресации)")
            return "FOLLOWME"
        
        # Звонок занятому менеджеру - множественные start ИЛИ bridge + start
        if starts > 1:
            # Проверяем что start события в начале последовательности
            start_events = [e for e in events[:10] if e.event == 'start']
            if len(start_events) > 1:
                self.logger.info("👥 Определен тип: BUSY_MANAGER (множественные start)")
                return "BUSY_MANAGER"
        
        # Дополнительная проверка: bridge в начале + start (активный разговор + новый звонок)
        first_5_events = events[:5]
        has_early_bridge = any(e.event == 'bridge' for e in first_5_events)
        has_start_with_trunk = any(e.event == 'start' and e.data.get('Trunk') for e in events)
        
        # Более строгая проверка: bridge должен быть ДО start события
        if has_early_bridge and has_start_with_trunk and starts >= 1:
            # Проверяем что bridge действительно ПЕРЕД start
            first_bridge_idx = None
            first_start_idx = None
            
            for i, event in enumerate(events):
                if event.event == 'bridge' and first_bridge_idx is None:
                    first_bridge_idx = i
                if event.event == 'start' and first_start_idx is None:
                    first_start_idx = i
                if first_bridge_idx is not None and first_start_idx is not None:
                    break
            
            # BUSY_MANAGER только если bridge идет ПЕРЕД start (активный разговор уже есть)
            if first_bridge_idx is not None and first_start_idx is not None and first_bridge_idx < first_start_idx:
                self.logger.info("👥 Определен тип: BUSY_MANAGER (активный bridge перед новым start)")
                return "BUSY_MANAGER"
        
        # Простой звонок
        self.logger.info("📞 Определен тип: SIMPLE")
        return "SIMPLE"
    
    # ───────────────────────────────────────────────────────────────────
    # ФИЛЬТРАЦИЯ ПРОСТЫХ ЗВОНКОВ
    # ───────────────────────────────────────────────────────────────────
    
    def _filter_simple_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """
        Фильтрация простых звонков (типы 1-1 до 2-17)
        
        Логика:
        - Bitrix24: start/dial → первый bridge → hangup
        - Telegram: start/dial → hangup
        """
        result = {'bitrix24': [], 'telegram': [], 'general': []}
        
        # Находим ключевые события
        start_event = None
        first_bridge = None
        hangup_event = None
        
        for event in events:
            if event.event in ['start', 'dial'] and event.uniqueid == primary_uid:
                if not start_event:  # Берем первый
                    start_event = event
            elif event.event == 'bridge' and primary_uid in event.get_related_uids():
                if not first_bridge:  # Берем первый bridge
                    first_bridge = event
            elif event.event == 'hangup' and event.uniqueid == primary_uid:
                hangup_event = event  # Берем последний (перезаписываем)
        
        # Для Bitrix24: start → bridge → hangup
        if start_event:
            result['bitrix24'].append(start_event)
            result['telegram'].append(start_event)
        
        if first_bridge:
            result['bitrix24'].append(first_bridge)
        
        if hangup_event:
            result['bitrix24'].append(hangup_event)
            result['telegram'].append(hangup_event)
        
        # General - ключевые события
        result['general'] = result['bitrix24'].copy()
        
        self.logger.debug(f"📞 Простая фильтрация: B24={len(result['bitrix24'])}, TG={len(result['telegram'])}")
        
        return result
    
    # ───────────────────────────────────────────────────────────────────
    # ЗАГЛУШКИ ДЛЯ СЛОЖНЫХ СЦЕНАРИЕВ (ЭТАПЫ 3-5)
    # ───────────────────────────────────────────────────────────────────
    
    def _filter_multiple_transfer_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """
        Фильтрация множественных переводов (тип 2-18)
        
        Анализирует 5 мостов множественного перевода A→B→C:
        1. start - показать карточку
        2. Первый bridge к основному UID - скрыть карточку (кто-то поднял)
        3. Значимые bridge при переводах - информационные обновления
        4. Финальный hangup основного UID - завершить звонок
        
        Логика: отфильтровываем консультационные мосты, оставляем только
        события связанные с основным потоком звонка.
        """
        self.logger.info(f"⚡ Фильтрация MULTIPLE_TRANSFER для основного UID: {primary_uid}")
        
        result = {'bitrix24': [], 'telegram': [], 'general': []}
        
        # Собираем все bridge_id для анализа
        bridge_ids = set()
        for event in events:
            if event.event in ['bridge_create', 'bridge', 'bridge_leave', 'bridge_destroy']:
                bridge_id = event.data.get('BridgeUniqueid', '')
                if bridge_id:
                    bridge_ids.add(bridge_id)
        
        self.logger.debug(f"📊 Найдено {len(bridge_ids)} уникальных мостов")
        
        # Анализируем каждый мост для определения его роли
        bridge_analysis = self._analyze_bridges(events, primary_uid, bridge_ids)
        main_bridge_id = bridge_analysis.get('main_bridge')
        
        self.logger.debug(f"🎯 Основной мост: {main_bridge_id}")
        
        # Находим ключевые события
        start_event = None
        first_bridge = None
        significant_bridges = []  # Значимые переводы
        hangup_event = None
        
        for event in events:
            # 1. Start событие
            if event.event == 'start' and event.uniqueid == primary_uid:
                start_event = event
                self.logger.debug(f"🚀 Найден start: {event.uniqueid}")
            
            # 2. Bridge события связанные с основным UID
            elif event.event == 'bridge':
                # Проверяем связь с основным UID через UniqueId или через номера телефонов
                is_related = (
                    event.uniqueid == primary_uid or 
                    self._is_bridge_related_to_primary(event, primary_uid, events)
                )
                
                if is_related:
                    bridge_id = event.data.get('BridgeUniqueid', '')
                    
                    if not first_bridge:
                        first_bridge = event
                        self.logger.debug(f"🌉 Первый bridge: {bridge_id}")
                    
                    # Значимые bridge - когда основной UID переключается между мостами
                    elif bridge_id == main_bridge_id or self._is_significant_bridge(event, bridge_analysis):
                        significant_bridges.append(event)
                        self.logger.debug(f"⚡ Значимый bridge: {bridge_id}")
            
            # 3. Финальный hangup основного звонка
            elif event.event == 'hangup' and event.uniqueid == primary_uid:
                hangup_event = event
                self.logger.debug(f"🏁 Финальный hangup: {event.uniqueid}")
        
        # Формируем результат для Bitrix24
        if start_event:
            result['bitrix24'].append(start_event)
            result['telegram'].append(start_event)
        
        if first_bridge:
            result['bitrix24'].append(first_bridge)
            
        # Добавляем значимые переводы для Bitrix24 (но не для Telegram)
        for bridge in significant_bridges:
            result['bitrix24'].append(bridge)
        
        if hangup_event:
            result['bitrix24'].append(hangup_event)
            result['telegram'].append(hangup_event)
        
        # General события - все ключевые
        result['general'] = result['bitrix24'].copy()
        
        self.logger.info(f"⚡ MULTIPLE_TRANSFER результат: B24={len(result['bitrix24'])}, TG={len(result['telegram'])}")
        
        return result
    
    def _analyze_bridges(self, events: List[Event], primary_uid: str, bridge_ids: set) -> Dict[str, Any]:
        """
        Анализирует мосты для определения их роли в множественном переводе
        
        Возвращает:
        - main_bridge: ID основного моста (где происходит финальное соединение)
        - consultation_bridges: IDs консультационных мостов
        - bridge_timeline: хронология мостов
        """
        bridge_usage = {}
        bridge_timeline = []
        
        # Анализируем использование каждого моста
        for bridge_id in bridge_ids:
            bridge_events = []
            for event in events:
                if event.data.get('BridgeUniqueid') == bridge_id:
                    bridge_events.append(event)
            
            if bridge_events:
                # Находим время создания моста
                create_times = [e.timestamp for e in bridge_events if e.event == 'bridge_create']
                create_time = min(create_times) if create_times else min(e.timestamp for e in bridge_events)
                
                # Находим время уничтожения моста
                destroy_times = [e.timestamp for e in bridge_events if e.event == 'bridge_destroy']
                destroy_time = max(destroy_times) if destroy_times else None
                
                bridge_usage[bridge_id] = {
                    'events': bridge_events,
                    'has_primary_uid': any(primary_uid in e.get_related_uids() for e in bridge_events),
                    'create_time': create_time,
                    'destroy_time': destroy_time
                }
                bridge_timeline.append((create_time, bridge_id))
        
        # Сортируем мосты по времени создания
        bridge_timeline.sort()
        
        # Основной мост - тот который последний взаимодействует с primary_uid перед hangup
        main_bridge = None
        for event in reversed(events):
            if (event.event == 'bridge' and 
                event.uniqueid == primary_uid and 
                event.data.get('BridgeUniqueid')):
                main_bridge = event.data.get('BridgeUniqueid')
                break
        
        # Консультационные мосты - все остальные
        consultation_bridges = [bid for bid in bridge_ids if bid != main_bridge]
        
        return {
            'main_bridge': main_bridge,
            'consultation_bridges': consultation_bridges,
            'bridge_timeline': bridge_timeline,
            'bridge_usage': bridge_usage
        }
    
    def _is_significant_bridge(self, event: Event, bridge_analysis: Dict) -> bool:
        """
        Определяет является ли bridge событие значимым для интеграций
        
        Значимые bridge:
        - Переключение основного UID между мостами (А→Б, Б→В)
        - Финальное соединение с последним получателем
        """
        bridge_id = event.data.get('BridgeUniqueid', '')
        
        # Если это основной мост - всегда значимо
        if bridge_id == bridge_analysis.get('main_bridge'):
            return True
        
        # Если это переключение между мостами - значимо
        # (можно добавить более сложную логику при необходимости)
        
        return False
    
    def _is_bridge_related_to_primary(self, bridge_event: Event, primary_uid: str, all_events: List[Event]) -> bool:
        """
        Определяет связан ли bridge событие с основным звонком
        
        Проверяет:
        1. Прямая связь через UniqueId
        2. Связь через номера телефонов (CallerIDNum, ConnectedLineNum)
        3. Связь через основной external номер звонка
        """
        # Прямая связь через UniqueId
        if bridge_event.uniqueid == primary_uid:
            return True
        
        # Получаем номер внешнего звонка из start события
        external_phone = None
        for event in all_events:
            if event.event == 'start' and event.uniqueid == primary_uid:
                external_phone = event.data.get('Phone', '')
                break
        
        if not external_phone:
            return False
        
        # Проверяем связь через номера в bridge событии
        caller = bridge_event.data.get('CallerIDNum', '')
        connected = bridge_event.data.get('ConnectedLineNum', '')
        
        # Если один из номеров в bridge == внешний номер основного звонка
        if external_phone in [caller, connected]:
            return True
        
        # Если номер начинается с "-" (форматирование Asterisk)
        formatted_external = f"-{external_phone}"
        if formatted_external in [caller, connected]:
            return True
        
        return False
    
    def _filter_busy_manager_events(self, events: List[Event]) -> Dict[str, List[Event]]:
        """
        Фильтрация звонков занятым менеджерам (типы 2-19-2-22)
        
        Обрабатывает сценарий:
        1. Менеджеры уже разговаривают между собой (активный bridge)
        2. Приходит новый внешний звонок к одному из занятых менеджеров
        3. Нужно показать только один звонок (обычно новый внешний)
        
        Логика: обнаруживаем активные внутренние разговоры и приоритизируем
        внешние звонки.
        """
        self.logger.info("👥 Фильтрация BUSY_MANAGER - поиск параллельных звонков к занятым менеджерам")
        
        result = {'bitrix24': [], 'telegram': [], 'general': []}
        
        # Анализируем структуру звонков
        call_analysis = self._analyze_busy_manager_calls(events)
        
        internal_calls = call_analysis['internal_calls']
        external_calls = call_analysis['external_calls']
        priority_call = call_analysis['priority_call']
        
        self.logger.debug(f"👥 Найдено: {len(internal_calls)} внутренних, {len(external_calls)} внешних звонков")
        self.logger.debug(f"🎯 Приоритетный звонок: {priority_call}")
        
        if not priority_call:
            # Если не удалось определить приоритет, используем простую логику
            self.logger.warning("👥 Не удалось определить приоритетный звонок, используем простую фильтрацию")
            primary_uid = self.get_primary_uniqueid(events)
            return self._filter_simple_events(events, primary_uid)
        
        # Фильтруем события только для приоритетного звонка
        priority_events = [e for e in events if self._is_event_related_to_call(e, priority_call)]
        
        # Определяем основной UID приоритетного звонка
        primary_uid = self._find_primary_uid_for_call(priority_events, priority_call)
        
        # Применяем соответствующую фильтрацию
        if priority_call['type'] == 'external':
            # Для внешнего звонка - полная фильтрация
            filtered = self._filter_simple_events(priority_events, primary_uid)
        else:
            # Для внутреннего звонка - минимальная фильтрация
            filtered = self._filter_internal_call_events(priority_events, primary_uid)
        
        self.logger.info(f"👥 BUSY_MANAGER результат: B24={len(filtered['bitrix24'])}, TG={len(filtered['telegram'])}")
        
        return filtered
    
    def _analyze_busy_manager_calls(self, events: List[Event]) -> Dict[str, Any]:
        """
        Анализирует структуру вызовов при занятых менеджерах
        
        Возвращает:
        - internal_calls: список внутренних звонков (номер→номер)
        - external_calls: список внешних звонков (внешний номер→внутренний)
        - priority_call: приоритетный звонок для отображения
        """
        internal_calls = []
        external_calls = []
        
        # Группируем start события по типам
        start_events = [e for e in events if e.event == 'start']
        
        for start_event in start_events:
            call_type = start_event.data.get('CallType', 0)
            phone = start_event.data.get('Phone', '')
            trunk = start_event.data.get('Trunk', '')
            
            call_info = {
                'uniqueid': start_event.uniqueid,
                'phone': phone,
                'trunk': trunk,
                'call_type': call_type,
                'timestamp': start_event.timestamp,
                'event': start_event
            }
            
            # Определяем тип звонка по наличию Trunk и CallType
            if trunk and call_type == 0:
                # Внешний входящий звонок (есть Trunk, CallType=0)
                call_info['type'] = 'external'
                external_calls.append(call_info)
            else:
                # Внутренний звонок (нет Trunk или CallType!=0)
                call_info['type'] = 'internal'
                internal_calls.append(call_info)
        
        # Определяем приоритетный звонок
        priority_call = self._determine_priority_call(internal_calls, external_calls, events)
        
        return {
            'internal_calls': internal_calls,
            'external_calls': external_calls,
            'priority_call': priority_call,
            'all_calls': internal_calls + external_calls
        }
    
    def _determine_priority_call(self, internal_calls: List[Dict], external_calls: List[Dict], events: List[Event]) -> Dict:
        """
        Определяет приоритетный звонок для отображения в интеграциях
        
        Логика приоритета:
        1. Внешние звонки имеют приоритет над внутренними
        2. Среди внешних - самый поздний (последний пришедший)
        3. Если только внутренние - самый ранний (первый активный)
        """
        if external_calls:
            # Приоритет внешним звонкам - берем последний
            priority_call = max(external_calls, key=lambda x: x['timestamp'])
            self.logger.debug(f"🎯 Выбран внешний звонок как приоритетный: {priority_call['phone']}")
            return priority_call
        
        elif internal_calls:
            # Если только внутренние - берем первый (самый ранний)
            priority_call = min(internal_calls, key=lambda x: x['timestamp'])
            self.logger.debug(f"🎯 Выбран внутренний звонок как приоритетный: {priority_call['phone']}")
            return priority_call
        
        else:
            self.logger.warning("🚨 Не найдено ни одного звонка для определения приоритета")
            return None
    
    def _is_event_related_to_call(self, event: Event, call_info: Dict) -> bool:
        """
        Определяет относится ли событие к конкретному звонку
        
        Проверяет связь через:
        1. UniqueId
        2. Номера телефонов
        3. Bridge связи
        """
        if not call_info:
            return False
        
        # Прямая связь через UniqueId
        if event.uniqueid == call_info['uniqueid']:
            return True
        
        # Связь через номера телефонов в bridge событиях
        if event.event in ['bridge', 'bridge_leave', 'bridge_create', 'bridge_destroy']:
            caller = event.data.get('CallerIDNum', '')
            connected = event.data.get('ConnectedLineNum', '')
            
            # Проверяем связь с номером звонка
            call_phone = call_info['phone']
            if call_phone in [caller, connected] or f"-{call_phone}" in [caller, connected]:
                return True
        
        # Связь через hangup с тем же номером
        if event.event == 'hangup':
            hangup_phone = event.data.get('Phone', '')
            if hangup_phone == call_info['phone']:
                return True
        
        return False
    
    def _find_primary_uid_for_call(self, events: List[Event], call_info: Dict) -> str:
        """
        Находит основной UniqueId для конкретного звонка
        """
        if call_info and call_info.get('uniqueid'):
            return call_info['uniqueid']
        
        # Резервный поиск через общую логику
        return self.get_primary_uniqueid(events)
    
    def _filter_internal_call_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """
        Упрощенная фильтрация для внутренних звонков
        
        Для внутренних звонков между менеджерами показываем минимум событий
        """
        result = {'bitrix24': [], 'telegram': [], 'general': []}
        
        for event in events:
            if event.event == 'start' and event.uniqueid == primary_uid:
                result['bitrix24'].append(event)
                result['telegram'].append(event)
            elif event.event == 'hangup' and event.uniqueid == primary_uid:
                result['bitrix24'].append(event)
                result['telegram'].append(event)
            elif event.event == 'bridge' and event.uniqueid == primary_uid:
                result['bitrix24'].append(event)  # Только для B24
        
        result['general'] = result['bitrix24'].copy()
        
        return result
    
    def _filter_followme_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """
        Фильтрация FollowMe переадресации (типы 2-23-2-30)
        
        Обрабатывает сценарий каскадной переадресации:
        1. Входящий звонок на внутренний номер
        2. FollowMe создает множественные исходящие звонки
        3. Звонки идут параллельно на внутренние + мобильные номера
        4. Нужно показать только основной звонок, скрыть переадресации
        
        Логика: находим основной входящий звонок и игнорируем все порожденные им
        переадресации (звонки с CallType=1).
        """
        self.logger.info("🌊 Фильтрация FOLLOWME - каскадная переадресация на множество номеров")
        
        result = {'bitrix24': [], 'telegram': [], 'general': []}
        
        # Анализируем структуру FollowMe переадресации
        followme_analysis = self._analyze_followme_calls(events, primary_uid)
        
        main_call = followme_analysis['main_call']
        redirect_calls = followme_analysis['redirect_calls']
        
        self.logger.debug(f"🌊 Основной звонок: {main_call}")
        self.logger.debug(f"🌊 Переадресации: {len(redirect_calls)} звонков")
        
        if not main_call:
            # Если не удалось определить основной звонок, используем простую логику
            self.logger.warning("🌊 Не удалось определить основной звонок, используем простую фильтрацию")
            return self._filter_simple_events(events, primary_uid)
        
        # Фильтруем только события основного звонка (игнорируем переадресации)
        main_events = [e for e in events if self._is_event_from_main_call(e, main_call, redirect_calls)]
        
        # Применяем простую фильтрацию к основному звонку
        filtered = self._filter_simple_events(main_events, primary_uid)
        
        self.logger.info(f"🌊 FOLLOWME результат: B24={len(filtered['bitrix24'])}, TG={len(filtered['telegram'])}")
        self.logger.info(f"🌊 Отфильтровано {len(redirect_calls)} переадресаций FollowMe")
        
        return filtered
    
    def _analyze_followme_calls(self, events: List[Event], primary_uid: str) -> Dict[str, Any]:
        """
        Анализирует структуру FollowMe переадресации
        
        Возвращает:
        - main_call: основной входящий звонок (CallType=0 с Trunk)
        - redirect_calls: список переадресаций (CallType=1 исходящие)
        """
        main_call = None
        redirect_calls = []
        
        # Ищем все start события
        start_events = [e for e in events if e.event == 'start']
        
        for start_event in start_events:
            call_type = start_event.data.get('CallType', 0)
            trunk = start_event.data.get('Trunk', '')
            phone = start_event.data.get('Phone', '')
            
            call_info = {
                'uniqueid': start_event.uniqueid,
                'phone': phone,
                'trunk': trunk,
                'call_type': call_type,
                'timestamp': start_event.timestamp,
                'event': start_event
            }
            
            # Основной звонок: CallType=0 + есть Trunk (входящий внешний)
            if call_type == 0 and trunk:
                if start_event.uniqueid == primary_uid:
                    main_call = call_info
                    self.logger.debug(f"🎯 Найден основной звонок: {phone} (UID: {start_event.uniqueid})")
            
            # Переадресация: CallType=1 (исходящий)
            elif call_type == 1:
                redirect_calls.append(call_info)
                self.logger.debug(f"📞 Найдена переадресация: {phone} (UID: {start_event.uniqueid})")
        
        return {
            'main_call': main_call,
            'redirect_calls': redirect_calls,
            'all_calls': [main_call] + redirect_calls if main_call else redirect_calls
        }
    
    def _is_event_from_main_call(self, event: Event, main_call: Dict, redirect_calls: List[Dict]) -> bool:
        """
        Определяет принадлежит ли событие основному звонку или переадресации
        
        Возвращает True если событие от основного звонка, False если от переадресации
        """
        if not main_call:
            return True  # Если основной звонок не определен, пропускаем все
        
        # Прямая связь с основным звонком
        if event.uniqueid == main_call['uniqueid']:
            return True
        
        # Исключаем события переадресаций
        redirect_uids = [call['uniqueid'] for call in redirect_calls]
        if event.uniqueid in redirect_uids:
            return False
        
        # Для bridge событий проверяем связь с основным номером
        if event.event in ['bridge', 'bridge_leave', 'bridge_create', 'bridge_destroy']:
            main_phone = main_call['phone']
            caller = event.data.get('CallerIDNum', '')
            connected = event.data.get('ConnectedLineNum', '')
            
            # Если событие связано с основным номером - принадлежит основному звонку
            if main_phone in [caller, connected] or f"-{main_phone}" in [caller, connected]:
                return True
            
            # Если событие связано с номерами переадресаций - исключаем
            for redirect in redirect_calls:
                redirect_phone = redirect['phone']
                if redirect_phone in [caller, connected]:
                    return False
        
        # Для dial событий исключаем исходящие переадресации
        if event.event == 'dial':
            call_type = event.data.get('CallType', 0)
            if call_type == 1:  # Исходящий звонок - переадресация
                return False
        
        # По умолчанию включаем в основной звонок
        return True

# ═══════════════════════════════════════════════════════════════════
# УТИЛИТЫ ДЛЯ СОЗДАНИЯ EVENT ОБЪЕКТОВ
# ═══════════════════════════════════════════════════════════════════

def create_event_from_asterisk_data(event_type: str, data: Dict[str, Any]) -> Event:
    """
    Создает объект Event из данных Asterisk
    
    Args:
        event_type: Тип события (start, dial, bridge, hangup, etc.)
        data: Словарь с данными от Asterisk
        
    Returns:
        Event объект
    """
    return Event(
        event=event_type,
        uniqueid=data.get('UniqueId', ''),
        timestamp=datetime.now(),
        data=data
    )

def create_events_from_call_sequence(call_data: List[Tuple[str, Dict]]) -> List[Event]:
    """
    Создает список Event объектов из последовательности событий звонка
    
    Args:
        call_data: Список кортежей (event_type, data)
        
    Returns:
        Список Event объектов
    """
    events = []
    for event_type, data in call_data:
        event = create_event_from_asterisk_data(event_type, data)
        events.append(event)
    
    return events

# ═══════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ФИЛЬТРА
# ═══════════════════════════════════════════════════════════════════

# Создаем глобальный экземпляр для использования в других модулях
event_filter = EventFilter()

def filter_events_for_integrations(events: List[Event]) -> FilterResult:
    """
    Удобная функция для вызова фильтрации
    
    Использует глобальный экземпляр EventFilter
    """
    return event_filter.filter_events_for_integrations(events)
