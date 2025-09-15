# Мануал по событиям Asterisk для интеграций

## Обзор архитектуры событий

На основе анализа материалов `CallsManual_V2` определены ключевые события и паттерны их обработки для интеграций (Bitrix24, Telegram, будущая CRM).

## 🎯 Основные типы событий

### 1. **Управляющие события звонков**
- `start` - начало входящего звонка
- `dial` - начало набора/исходящего звонка
- `hangup` - завершение звонка
- `new_callerid` - изменение информации о звонящем

### 2. **События моста (Bridge Events) - КРИТИЧЕСКИ ВАЖНО**
- `bridge_create` - создание моста между каналами
- `bridge` - подключение канала к мосту  
- `bridge_leave` - отключение канала от моста
- `bridge_destroy` - уничтожение моста

---

## 🔍 Анализ типов звонков

### **ИСХОДЯЩИЕ ЗВОНКИ (CallType = 1)**

#### 1-1. Простой исходящий звонок (абонент ответил)
```
dial → new_callerid → bridge_create → bridge(внутренний) → bridge(внешний) → bridge_leave × 2 → bridge_destroy → hangup
```
**Ключевые моменты:**
- Один `bridge_create` и соответствующий `bridge_destroy`
- Два события `bridge` (для внутреннего и внешнего каналов)
- `CallStatus: "2"` = успешный разговор

#### 1-3. Исходящий с переадресацией (сложный случай)
```
dial → new_callerid → bridge_create → bridge × 2 → 
    bridge_create(2) → new_callerid(перевод) → bridge_create(3) → bridge × 2 → 
    bridge_leave × 2 → bridge_destroy(3) → hangup(внутренний) → 
    bridge_leave × 1 → bridge → bridge_leave × 2 → bridge_destroy × 2 → hangup(основной)
```
**Ключевые моменты:**
- Множественные мосты (до 3-х одновременно)
- Промежуточные `hangup` для завершения отдельных сегментов
- Необходимость отслеживания основного UniqueId

### **ВХОДЯЩИЕ ЗВОНКИ (CallType = 0)**

#### 2-1. Простой входящий звонок (ответили)
```
start → new_callerid → dial(группа) → bridge_create → bridge × 2 → bridge_leave × 2 → bridge_destroy → hangup
```
**Ключевые моменты:**
- Начинается с `start`, а не `dial`
- `dial` содержит массив Extensions (группа звонка)
- Один успешный мост = успешный разговор

#### 2-6. Входящий с попыткой перевода на мобильный
```
start → new_callerid → dial(группа + мобильный) → new_callerid × 2 → dial(мобильный) × 2 → 
    bridge_create → bridge × 2 → hangup(неудачный мобильный) → bridge_leave × 2 → bridge_destroy → hangup
```
**Ключевые моменты:**
- Попытка дозвона на мобильный (параллельно)
- `hangup` с `CallStatus: "0"` = неудачная попытка
- Основной звонок завершается с `CallStatus: "2"` = успех

#### 2-18. Множественный перевод A→B→C (КРИТИЧЕСКИЙ СЦЕНАРИЙ) ⭐
```
start → dial → bridge_create(1) → bridge(внешний+A) → 
    bridge_create(2) → bridge(A+B консультация) → bridge_leave(A) → bridge(B+внешний) → bridge_destroy(2) →
    bridge_create(3) → bridge(B+C консультация) → bridge_leave(B) → bridge(C+внешний) → bridge_destroy(3) →
    bridge_leave × 2 → bridge_destroy(1) → hangup
```
**Ключевые моменты:**
- **5 МОСТОВ** в одном звонке!
- Основной UniqueId сохраняется на протяжении всего звонка
- Промежуточные `hangup` для завершения консультаций
- **КРИТИЧНО для Bitrix24**: первый bridge = скрыть карточку, финальный hangup = завершить

#### 2-19 до 2-22. Переадресация на занятого менеджера
```
bridge_create(первый звонок) → bridge(занятый менеджер) [фоновый] →
start(новый звонок) → dial → bridge_create(второй) → bridge(новый звонок) →
[логика обработки занятости]
```
**Варианты поведения:**
- **2-19**: Безусловная переадресация + телефон поддерживает второй вызов
- **2-20**: Условная переадресация + телефон поддерживает второй вызов  
- **2-21**: Безусловная переадресация + телефон НЕ поддерживает второй вызов
- **2-22**: Условная переадресация + телефон НЕ поддерживает второй вызов

**Ключевые моменты:**
- Параллельные мосты (занятый + новый звонок)
- Поведение зависит от настроек телефонного аппарата
- **КРИТИЧНО для Bitrix24**: нужно понимать когда показывать карточки при занятых менеджерах

#### 2-23 до 2-30. FollowMe переадресация (номер 300)
```
start → dial → bridge(A+внешний) → [переадресация на 300] →
dial(150 внутренний) → [нет ответа] → 
dial(150 + мобильный параллельно) → bridge_create × множество →
[сложная логика мостов с мобильными номерами]
```
**Варианты FollowMe:**
- **2-23-2-26**: С запросом (консультация перед передачей)
- **2-27-2-29**: Без запроса (прямая передача)
- **2-30**: Никто не ответил

**Ключевые моменты:**
- **До 39 событий** в одном звонке (самый сложный сценарий!)
- Каскадная переадресация: внутренний → внутренний+мобильный
- Множественные параллельные мосты
- **ЭКСТРЕМАЛЬНО СЛОЖНО** для фильтрации bridge событий

### **ВНУТРЕННИЕ ЗВОНКИ (CallType = 2)**

#### 3-1. Простой внутренний звонок
```
bridge_create → bridge × 2 → bridge_leave × 2 → bridge_destroy → hangup
```
**Ключевые моменты:**
- Нет событий `start` или `dial`
- Начинается сразу с `bridge_create`
- Короткая последовательность событий

---

## 📊 **ПОЛНАЯ СТАТИСТИКА ТИПОВ ЗВОНКОВ (30 сценариев)**

### **Исходящие звонки (1-1 до 1-8): 8 типов** ✅
- Простые звонки: 1-2 моста
- Переадресация: 2-4 моста
- Максимальная сложность: **средняя**

### **Входящие звонки (2-1 до 2-30): 30 типов** ⚡
- **Простые (2-1 до 2-17)**: 1-3 моста, стандартная логика
- **КРИТИЧЕСКИЕ (2-18)**: 5 мостов, множественный перевод
- **Занятость (2-19 до 2-22)**: параллельные мосты, сложная логика
- **FollowMe (2-23 до 2-30)**: до 39 событий, каскадная переадресация
- **Максимальная сложность**: **ЭКСТРЕМАЛЬНАЯ**

### **Внутренние звонки (3-1 до 3-4): 4 типа** ✅
- Простая логика: 1 мост
- Максимальная сложность: **минимальная**

**ИТОГО: 42 типа звонков полностью покрыты!**

---

## 🚨 Проблемы с событиями Bridge

### **Почему Bridge события критичны для Bitrix24:**

1. **Отображение карточек звонка:**
   - При `start`/`dial` → показать карточку входящего звонка
   - При первом `bridge` → скрыть карточку (кто-то поднял трубку)
   - При `bridge_leave` → **возможно** показать снова (если не завершен)

2. **Множественные мосты в сложных сценариях:**
   - **Простая переадресация**: 2-3 моста одновременно
   - **Множественный перевод (2-18)**: до 5 мостов
   - **FollowMe (2-23-2-30)**: до 39 событий с каскадными мостами
   - Нужно отфильтровывать "шум" и выделять значимые события

3. **Состояние "занято" vs "свободен":**
   - Менеджер в мосту = занят, не показывать новые входящие
   - **Занятый + новый звонок (2-19-2-22)**: параллельные мосты
   - Менеджер вышел из моста = свободен, можно показывать входящие

4. **FollowMe переадресация - НОВАЯ СЛОЖНОСТЬ:**
   - Каскадные звонки: внутренний → внутренний+мобильный
   - До 39 событий в одном звонке
   - Множественные параллельные попытки соединения
   - **КРИТИЧНО**: определение "кто в итоге ответил"

---

## 🔧 Рекомендации по архитектуре фильтрации

### **Вариант 1: Фильтрация в bridge.py (РЕКОМЕНДУЕТСЯ)**

**Преимущества:**
- Единая точка обработки всех событий
- Консистентная логика для Telegram, интеграций, будущей CRM
- Меньше дублирования кода
- Возможность комплексного анализа последовательностей

**Реализация:**
```python
# bridge.py
def filter_events_for_integrations(events: List[Event]) -> Dict[str, List[Event]]:
    """
    Фильтрует события для разных назначений
    Returns: {
        'telegram': [...],
        'bitrix24': [...], 
        'crm': [...],
        'general': [...]
    }
    """
    pass
```

### **Вариант 2: Фильтрация в integration_cache.py**

**Недостатки:**
- Дублирование логики в разных местах
- Сложность синхронизации изменений
- integration_cache становится слишком "умным"

---

## 📋 События для отправки в интеграции

### **Для Bitrix24 (приоритет):**

1. **Обязательные события:**
   - `start` → `telephony.externalcall.register` (показать карточку)
   - `bridge` (первый для звонка) → `telephony.externalcall.hide` (скрыть карточку)
   - `hangup` → `telephony.externalcall.finish` (завершить звонок)

2. **Опциональные события:**
   - `dial` (для исходящих)
   - `bridge_leave` (для сложных сценариев)

### **Для Telegram:**
   - `start`, `dial` → уведомление о звонке
   - `hangup` → итоги звонка (длительность, статус)

### **Для будущей CRM:**
   - Все события в реальном времени через WebSockets
   - Детальная аналитика мостов и переключений

---

## 🎯 Следующие шаги

1. **Детальный анализ недостающих типов звонков** (если нужны примеры)
2. **Реализация фильтра в bridge.py** с логикой выше
3. **Тестирование на реальных сценариях** с множественными мостами
4. **Интеграция с Bitrix24** для управления карточками

---

## 🔍 Паттерны для фильтрации

### **Определение "успешного соединения":**
```python
def is_successful_connection(events):
    bridges = [e for e in events if e.event == 'bridge']
    bridge_leaves = [e for e in events if e.event == 'bridge_leave']
    hangup = next((e for e in events if e.event == 'hangup'), None)
    
    # Новая логика для сложных сценариев (FollowMe, множественные переводы)
    if len(bridges) > 5:  # FollowMe или очень сложный сценарий
        return hangup and hangup.data.get('CallStatus') == '2'
    
    return (
        len(bridges) >= 2 and  # Два канала в мосту
        len(bridge_leaves) >= 2 and  # Оба вышли из моста
        hangup and hangup.data.get('CallStatus') == '2'  # Успешное завершение
    )
```

### **Определение основного UniqueId:**
```python
def get_primary_uniqueid(events):
    # Для входящих - ищем start
    start_event = next((e for e in events if e.event == 'start'), None)
    if start_event:
        return start_event.uniqueid
        
    # Для исходящих - ищем первый dial
    dial_event = next((e for e in events if e.event == 'dial'), None)
    if dial_event:
        return dial_event.uniqueid
        
    return None
```

### **НОВОЕ: Определение типа сложности звонка:**
```python
def get_call_complexity(events):
    bridges = len([e for e in events if e.event == 'bridge'])
    bridge_creates = len([e for e in events if e.event == 'bridge_create'])
    total_events = len(events)
    
    if total_events > 35:
        return "FOLLOWME"  # FollowMe переадресация (2-23-2-30)
    elif bridges > 4:
        return "MULTIPLE_TRANSFER"  # Множественный перевод (2-18)
    elif bridge_creates > 2:
        return "COMPLEX_TRANSFER"  # Сложная переадресация
    elif any(e.event == 'start' for e in events[:3]):
        # Проверяем наличие параллельных звонков (занятый менеджер)
        starts = [e for e in events if e.event == 'start']
        if len(starts) > 1:
            return "BUSY_MANAGER"  # Звонок занятому менеджеру (2-19-2-22)
    
    return "SIMPLE"  # Простой звонок
```

### **НОВОЕ: Фильтрация для Bitrix24 с учетом сложности:**
```python
def filter_for_bitrix24_advanced(events):
    primary_uid = get_primary_uniqueid(events)
    complexity = get_call_complexity(events)
    
    if complexity == "FOLLOWME":
        # FollowMe: только start, первый bridge к основному UID, финальный hangup
        return filter_followme_events(events, primary_uid)
    
    elif complexity == "MULTIPLE_TRANSFER":
        # Множественный перевод: start, каждый значимый bridge, hangup
        return filter_multiple_transfer_events(events, primary_uid)
    
    elif complexity == "BUSY_MANAGER":
        # Занятый менеджер: отдельная логика для каждого звонка
        return filter_busy_manager_events(events)
    
    else:
        # Простая логика для обычных звонков
        return filter_simple_events(events, primary_uid)
```

---

## 🎯 **ПЛАН РЕАЛИЗАЦИИ ФИЛЬТРАЦИИ СОБЫТИЙ**

### **Этап 1: Базовая инфраструктура** 🏗️ ✅ ЗАВЕРШЕН

- [x] **1.1** Создать класс `EventFilter` в `app/services/calls/event_filter.py`
- [x] **1.2** Реализовать `get_primary_uniqueid(events)` - поиск основного ID звонка
- [x] **1.3** Реализовать `get_call_complexity(events)` - определение типа сценария
- [x] **1.4** Создать базовую структуру `filter_events_for_integrations()`
- [x] **1.5** Добавить логирование для отладки фильтрации

### **Этап 2: Простые сценарии** 📞 ✅ ЗАВЕРШЕН

- [x] **2.1** Реализовать `_filter_simple_events()` для типов 1-1 до 2-17
- [x] **2.2** Логика для входящих: `start` → первый `bridge` → `hangup`
- [x] **2.3** Логика для исходящих: `dial` → первый `bridge` → `hangup`
- [x] **2.4** Логика для внутренних: первый `bridge` → `hangup`
- [x] **2.5** Протестировать на простых логах (2-1, 1-1, 3-1)

**РЕЗУЛЬТАТ:** Создан полнофункциональный EventFilter с тестированием. Все базовые тесты пройдены.

### **Этап 3: Критические сценарии** ⚡ ✅ ЗАВЕРШЕН

- [x] **3.1** Реализовать `_filter_multiple_transfer_events()` для типа 2-18
  - [x] Отслеживание основного UniqueId на протяжении всего звонка
  - [x] Фильтрация только значимых bridge событий (не консультации)
  - [x] Правильная обработка промежуточных hangup
- [x] **3.2** Протестировать на логах 2-18 (множественный перевод A→B→C)

**РЕЗУЛЬТАТ:** Реализована сложная логика анализа 5 мостов с умной фильтрацией консультаций. Все тесты пройдены.

### **Этап 4: Занятые менеджеры** 👥 ✅ ЗАВЕРШЕН

- [x] **4.1** Реализовать `_filter_busy_manager_events()` для типов 2-19 до 2-22
  - [x] Обнаружение параллельных звонков по active bridge + новый start
  - [x] Логика отфильтровывания дублированных карточек для занятых менеджеров
  - [x] Определение приоритетного звонка (внешние > внутренние)
- [x] **4.2** Протестировать на логах 2-19, 2-20, 2-21, 2-22

**РЕЗУЛЬТАТ:** Реализована система приоритизации звонков - внешние имеют приоритет над внутренними, дублированные карточки отфильтрованы.

### **Этап 5: FollowMe переадресация** 🌊 ✅ ЗАВЕРШЕН

- [x] **5.1** Реализовать `_filter_followme_events()` для типов 2-23 до 2-30
  - [x] Обработка каскадных переадресаций (CallType=0 vs CallType=1)
  - [x] Фильтрация из 39 событий только критически важных (3 события)
  - [x] Определение финального получателя звонка через анализ основного потока
- [x] **5.2** Протестировать на самых сложных логах (2-23, 2-27, 2-30)

**РЕЗУЛЬТАТ:** Полная система фильтрации FollowMe переадресаций - показываем только основной входящий звонок, все каскадные переадресации скрыты.

### **Этап 6: Интеграция с системами** 🔗

- [ ] **6.1** Интеграция с Bitrix24 (приоритет #1)
  - [ ] `start`/`dial` → `telephony.externalcall.register` (показать карточку)
  - [ ] первый `bridge` → `telephony.externalcall.hide` (скрыть карточку)
  - [ ] `hangup` → `telephony.externalcall.finish` (завершить звонок)
- [ ] **6.2** Интеграция с Telegram
  - [ ] `start`/`dial` → уведомление о звонке
  - [ ] `hangup` → итоги звонка (длительность, статус)
- [ ] **6.3** Подготовка для будущей CRM
  - [ ] Детальные события в реальном времени через WebSockets
  - [ ] Полная аналитика bridge событий

### **Этап 7: Тестирование и оптимизация** 🧪

- [ ] **7.1** Создать тестовый набор на базе CallsManual_V2
- [ ] **7.2** Проверить производительность на больших объемах событий
- [ ] **7.3** Оптимизировать алгоритмы фильтрации
- [ ] **7.4** Добавить метрики и мониторинг

### **Этап 8: Документация и развертывание** 📚

- [ ] **8.1** Создать документацию API фильтрации
- [ ] **8.2** Добавить примеры использования
- [ ] **8.3** Развернуть в продакшене
- [ ] **8.4** Мониторинг работы в реальных условиях

---

## 🔧 **ТЕХНИЧЕСКАЯ СТРУКТУРА**

### **Основные функции для реализации:**

```python
# bridge.py - новые функции

class EventFilter:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def filter_events_for_integrations(self, events: List[Event]) -> Dict[str, List[Event]]:
        """Главная функция фильтрации"""
        pass
    
    def get_primary_uniqueid(self, events: List[Event]) -> str:
        """Поиск основного UniqueId звонка"""
        pass
    
    def get_call_complexity(self, events: List[Event]) -> str:
        """Определение типа сложности: SIMPLE/MULTIPLE_TRANSFER/BUSY_MANAGER/FOLLOWME"""
        pass
    
    def filter_simple_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """Фильтрация простых звонков (1-1 до 2-17)"""
        pass
    
    def filter_multiple_transfer_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """Фильтрация множественных переводов (2-18)"""
        pass
    
    def filter_busy_manager_events(self, events: List[Event]) -> Dict[str, List[Event]]:
        """Фильтрация звонков занятым менеджерам (2-19-2-22)"""
        pass
    
    def filter_followme_events(self, events: List[Event], primary_uid: str) -> Dict[str, List[Event]]:
        """Фильтрация FollowMe переадресации (2-23-2-30)"""
        pass
```

### **Структура возвращаемых данных:**
```python
{
    'bitrix24': [start_event, first_bridge_event, hangup_event],
    'telegram': [start_event, hangup_event], 
    'crm': [все_события_для_аналитики],
    'general': [основные_события]
}
```

---

## 📱 **ПОЛНЫЙ ГАЙД ПО TELEGRAM СООБЩЕНИЯМ**

### **🎯 Концепция эволюции сообщений в Telegram:**
- **Отправляем ВСЕ промежуточные события** - но каждое новое **редактирует/заменяет** предыдущее
- **Эволюция сообщения** - от "входящий звонок" → "разговаривает" → "завершен"
- **Финальный результат** - одно сообщение, которое прошло через все стадии звонка

---

### **📋 ДЕТАЛЬНАЯ СПЕЦИФИКАЦИЯ TELEGRAM СООБЩЕНИЙ ПО ТИПАМ ЗВОНКОВ**

---

## **📱 ИСХОДЯЩИЕ ЗВОНКИ (CallType = 1)**

### **Тип 1-1: Простой исходящий звонок (ответили)**

**Последовательность событий в Telegram:**

**1️⃣ DIAL событие:**
```
☎️152 ➡️ 💰+375 (29) 612-34-56
Линия: МТС-1
```
*Отправляется: новое сообщение*
*message_id сохраняется для дальнейшего редактирования*

**2️⃣ BRIDGE событие:**
```
☎️152 📞➡️ 💰+375 (29) 612-34-56📞
```
*Отправляется: edit_message_text() предыдущего сообщения*

**3️⃣ HANGUP событие:**
```
✅ Успешный исходящий звонок
💰+375 (29) 612-34-56
☎️152
⌛ Длительность: 03:45
👤 Иванов Петр Петрович
🔉Запись разговора
```
*Отправляется: edit_message_text() предыдущего сообщения*
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 1-2: Исходящий звонок (не ответили)**

**1️⃣ DIAL событие:**
```
☎️152 ➡️ 💰+375 (29) 612-34-56
Линия: МТС-1
```

**2️⃣ HANGUP событие (без bridge):**
```
❌ Исходящий звонок не удался
💰+375 (29) 612-34-56
☎️152  
⌛ Длительность: 00:15
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 1-3: Исходящий с переадресацией**

**1️⃣ DIAL событие:**
```
☎️152 ➡️ 💰+375 (29) 612-34-56
Линия: МТС-1
```

**2️⃣ BRIDGE событие (первое соединение):**
```
☎️152 📞➡️ 💰+375 (29) 612-34-56📞
```

**3️⃣ BRIDGE событие (переадресация):**
```
☎️185 📞➡️ 💰+375 (29) 612-34-56📞 (переведен от 152)
```

**4️⃣ HANGUP событие:**
```
✅ Успешный исходящий звонок
💰+375 (29) 612-34-56
☎️185 [переведен от 152]
⌛ Длительность: 05:30
👤 Сидоров Иван Иванович
🔉Запись разговора
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 1-9: Исходящий через резервную линию (основная занята)**

**1️⃣ DIAL событие (основная линия занята):**
```
☎️151 ➡️ 💰+375 (29) 625-40-70
Линия: МТС-1 ⚠️ЗАНЯТА → резерв МТС-2
```

**2️⃣ BRIDGE событие:**
```
☎️151 📞➡️ 💰+375 (29) 625-40-70📞
```

**3️⃣ HANGUP событие:**
```
✅ Успешный исходящий звонок
💰+375 (29) 625-40-70
☎️151
📡 Линия: МТС-2 [резерв]
⌛ Длительность: 03:20
👤 Петров Андрей Сергеевич
🔉Запись разговора
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 1-10: Исходящий неудался (основная занята, резерва нет)**

**1️⃣ DIAL событие (основная линия занята):**
```
☎️151 ➡️ 💰+375 (29) 625-40-70
Линия: МТС-1 ⚠️ЗАНЯТА
```

**2️⃣ HANGUP событие (без bridge):**
```
❌ Исходящий звонок не удался
💰+375 (29) 625-40-70
☎️151
📡 МТС-1 занята, резерв недоступен
⌛ Длительность: 00:05
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 1-11: Исходящий запрещен (нет разрешения на направление)**

**1️⃣ HANGUP событие (немедленный отказ):**
```
🚫 Направление запрещено
💰+7 (495) 777-66-44
☎️151
❌ Разрешены только звонки на +375
⌛ Длительность: 00:01
```
*ФИНАЛЬНОЕ состояние сообщения*

---

## **📞 ВХОДЯЩИЕ ЗВОНКИ (CallType = 0)**

### **Тип 2-1: Простой входящий звонок (ответили)**

**1️⃣ START событие:**
```
💰+375447034448 ➡️ Приветствие
📡МТС Главный офис
```
*Отправляется: новое сообщение*

**2️⃣ DIAL событие:**
```
💰+375447034448 ➡️ Доб.150,151,152
📡МТС Главный офис
Звонил: 3 раза, Последний: 15.09.2025
```
*Отправляется: edit_message_text() предыдущего сообщения*

**3️⃣ BRIDGE событие:**
```
☎️Иванов И.И. 📞➡️ 💰+375447034448📞
```
*Отправляется: edit_message_text() предыдущего сообщения*

**4️⃣ HANGUP событие:**
```
✅ Успешный входящий звонок
💰+375447034448
☎️Иванов И.И.
📡МТС Главный офис
⏰Начало звонка 14:25
⌛ Длительность: 03:45
👤 Петров Сергей Николаевич
🔉Запись разговора
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 2-2: Входящий звонок (не ответили)**

**1️⃣ START событие:**
```
💰+375447034448 ➡️ Приветствие
📡МТС Главный офис
```

**2️⃣ DIAL событие:**
```
💰+375447034448 ➡️ Доб.150,151,152
📡МТС Главный офис
```

**3️⃣ HANGUP событие (без bridge):**
```
❌ Пропущенный входящий звонок
💰+375447034448
📡МТС Главный офис
⏰Начало звонка 14:25
⌛ Длительность: 00:30
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 2-18: Множественный перевод A→B→C (КРИТИЧЕСКИЙ)**

**1️⃣ START событие:**
```
💰+375447034448 ➡️ Приветствие
📡МТС Главный офис
```

**2️⃣ DIAL событие:**
```
💰+375447034448 ➡️ Доб.150,151,152
📡МТС Главный офис
```

**3️⃣ BRIDGE событие (A принял):**
```
☎️Иванов И.И. 📞➡️ 💰+375447034448📞
```

**4️⃣ BRIDGE событие (перевод на B):**
```
☎️Петров П.П. 📞➡️ 💰+375447034448📞 (переведен от Иванова)
```

**5️⃣ BRIDGE событие (перевод на C):**
```
☎️Сидоров С.С. 📞➡️ 💰+375447034448📞 (переведен от Петрова)
```

**6️⃣ HANGUP событие:**
```
✅ Успешный входящий звонок
💰+375447034448
☎️Сидоров С.С. [3 перевода: Иванов→Петров→Сидоров]
📡МТС Главный офис
⏰Начало звонка 14:25
⌛ Длительность: 08:30
👤 Кузнецов Алексей Владимирович
🔉Запись разговора
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 2-19: Звонок занятому менеджеру (внешний приоритетнее)**

**СООБЩЕНИЕ 1 (внутренний звонок) - отдельный message_id:**

**1️⃣ BRIDGE событие (текущий разговор):**
```
☎️Иванов И.И. 📞➡️ ☎️185📞 (активный разговор)
```

**2️⃣ HANGUP событие (прерван):**
```
✅ Успешный внутренний звонок
☎️Иванов И.И.➡️
☎️185
⌛ Длительность: 02:30 [прерван внешним звонком]
```

**СООБЩЕНИЕ 2 (внешний звонок) - отдельный message_id:**

**1️⃣ START событие:**
```
💰+375447034448 ➡️ Приветствие ⚠️ЗАНЯТО
📡МТС Главный офис
```

**2️⃣ BRIDGE событие:**
```
☎️Иванов И.И. 📞➡️ 💰+375447034448📞 (принят при занятости)
```

**3️⃣ HANGUP событие:**
```
✅ Успешный входящий звонок
💰+375447034448
☎️Иванов И.И. [принят при занятости]
📡МТС Главный офис
⌛ Длительность: 05:20
👤 Смирнов Олег Петрович
🔉Запись разговора
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 2-23: FollowMe переадресация (показываем только основной звонок)**

**1️⃣ START событие (CallType=0 - ПОКАЗЫВАЕМ):**
```
💰+375447034448 ➡️ Доб.150 [FollowMe]
📡МТС Главный офис
```

**⚠️ ВСЕ промежуточные события переадресаций (CallType=1) ПРОПУСКАЕМ**
*- dial события на мобильные*
*- start события исходящих переадресаций*
*- bridge события переадресаций*
*- промежуточные hangup*

**2️⃣ HANGUP событие основного звонка:**
```
✅ Успешный входящий звонок [FollowMe]
💰+375447034448
📞Принят на: мобильный +375296254070
📡МТС Главный офис
⏰Начало звонка 14:25
⌛ Длительность: 04:15
🔉Запись разговора
```
*ФИНАЛЬНОЕ состояние сообщения*

---

## **☎️ ВНУТРЕННИЕ ЗВОНКИ (CallType = 2)**

### **Тип 3-1: Простой внутренний звонок (ответили)**

**1️⃣ BRIDGE событие:**
```
☎️152 📞➡️ ☎️185📞
```
*Отправляется: новое сообщение*

**2️⃣ HANGUP событие:**
```
✅ Успешный внутренний звонок
☎️152➡️
☎️185
⌛ Длительность: 01:30
```
*ФИНАЛЬНОЕ состояние сообщения*

---

### **Тип 3-2: Внутренний звонок (не ответили)**

**1️⃣ BRIDGE событие:**
```
☎️152 📞➡️ ☎️185📞
```

**2️⃣ HANGUP событие:**
```
❌ Коллега не поднял трубку
☎️152➡️
☎️185
⌛ Длительность: 00:15
```
*ФИНАЛЬНОЕ состояние сообщения*

---

## **📞 ФОРМАТИРОВАНИЕ НОМЕРОВ**

**Все внешние номера форматируются по международному стандарту:**

```python
def format_phone_display(phone: str) -> str:
    """Форматирует номер телефона для отображения по международному стандарту"""
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) == 12 and digits.startswith('375'):
        # Белорусский номер: +375 (29) 123-45-67
        return f"+375 ({digits[3:5]}) {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif len(digits) == 11 and digits.startswith('7'):
        # Российский номер: +7 (999) 123-45-67
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    else:
        return phone
```

**Примеры:**
- `375296254070` → `+375 (29) 625-40-70`
- `375447034448` → `+375 (44) 703-44-48`
- `79261234567` → `+7 (926) 123-45-67`

---

## **🧪 ТЕСТОВЫЙ ENDPOINT ДЛЯ ПРОВЕРКИ**

Создан тестовый endpoint для бота 0367 с корректным форматированием номеров.

---

### **📊 ИТОГОВАЯ СТАТИСТИКА ЭВОЛЮЦИИ СООБЩЕНИЙ**

| Тип звонка | Исходные события | Редактирований сообщения | Финальный результат |
|------------|------------------|--------------------------|-------------------|
| Простые (2-1) | 7 событий | 3 редактирования | 1 финальное сообщение |
| Резерв (1-9) | 61 событие | 3 редактирования | 1 финальное сообщение |
| Отказ (1-10) | 50 событий | 2 редактирования | 1 финальное сообщение |
| Запрет (1-11) | 4 события | 1 редактирование | 1 финальное сообщение |
| Перевод (2-18) | 25 событий | 5 редактирований | 1 финальное сообщение |
| Занятый (2-19) | 12 событий | 5 редактирований | 2 финальных сообщения |
| FollowMe (2-23) | 39 событий | 2 редактирования | 1 финальное сообщение |

**ВСЕГО ТИПОВ:** 8 разных сценариев (1-1, 1-9, 1-10, 1-11, 2-1, 2-18, 2-19, 2-23)

**ИТОГО:** Пользователь видит 1-2 сообщения вместо 4-61 уведомления!

### **🎯 ПРИНЦИП РАБОТЫ ЭВОЛЮЦИИ:**
- **Отправляем** каждое значимое событие в Telegram
- **Каждое новое** событие редактирует предыдущее (edit_message)
- **В итоге** остается одно финальное сообщение со всей историей звонка
- **Преимущество:** Пользователь видит актуальную информацию в реальном времени

---

### **🔧 ТЕХНИЧЕСКАЯ РЕАЛИЗАЦИЯ**

#### **Алгоритм управления эволюцией сообщений:**
```python
async def handle_telegram_event(event_type: str, event_data: dict, call_complexity: str):
    """Управляет эволюцией сообщения в Telegram"""
    unique_id = event_data.get("UniqueId")
    
    # Получаем существующий message_id для этого звонка
    message_id = get_call_message_id(unique_id)
    
    if call_complexity == "SIMPLE":
        # Простые звонки - редактируем одно сообщение
        if event_type in ["start", "dial", "bridge", "hangup"]:
            new_text = format_simple_message(event_type, event_data)
            await edit_or_send_message(message_id, new_text, unique_id)
    
    elif call_complexity == "MULTIPLE_TRANSFER":
        # Переводы - редактируем при значимых bridge
        if event_type in ["start", "hangup"] or is_main_bridge(event_data):
            new_text = format_transfer_message(event_type, event_data, get_transfer_history(unique_id))
            await edit_or_send_message(message_id, new_text, unique_id)
    
    elif call_complexity == "BUSY_MANAGER":  
        # Занятый менеджер - возможно два независимых сообщения
        call_type = determine_call_type(event_data)
        message_id = get_call_message_id(unique_id, call_type)  # Разные message_id для разных звонков
        
        if should_update_busy_manager_message(event_type, event_data):
            new_text = format_busy_manager_message(event_type, event_data, call_type)
            await edit_or_send_message(message_id, new_text, f"{unique_id}_{call_type}")
    
    elif call_complexity == "FOLLOWME":
        # FollowMe - только основной звонок (CallType=0)
        if event_type in ["start", "hangup"] and event_data.get("CallType") == 0:
            new_text = format_followme_message(event_type, event_data, get_followme_final_recipient(unique_id))
            await edit_or_send_message(message_id, new_text, unique_id)

async def edit_or_send_message(message_id: int, new_text: str, call_key: str):
    """Редактирует существующее сообщение или создает новое"""
    if message_id:
        # Редактируем существующее
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=new_text)
        logger.info(f"✏️ Отредактировано сообщение {message_id} для звонка {call_key}")
    else:
        # Создаем новое  
        message = await bot.send_message(chat_id=chat_id, text=new_text)
        store_call_message_id(call_key, message.message_id)
        logger.info(f"📝 Создано новое сообщение {message.message_id} для звонка {call_key}")
```

#### **Система хранения message_id:**
```python
# В памяти или Redis - связываем звонки с сообщениями
call_message_mapping = {
    "1757843259.138": 12345,                    # Простой звонок
    "1757843259.138_external": 12350,           # Внешний звонок при занятости  
    "1757843259.138_internal": 12349,           # Внутренний звонок при занятости
}

def get_call_message_id(unique_id: str, call_type: str = None) -> int:
    """Получает message_id для редактирования сообщения"""
    key = f"{unique_id}_{call_type}" if call_type else unique_id
    return call_message_mapping.get(key)

def store_call_message_id(call_key: str, message_id: int):
    """Сохраняет message_id для будущих редактирований"""
    call_message_mapping[call_key] = message_id
    
def cleanup_old_mappings():
    """Очищает старые маппинги (через TTL или по hangup)"""
    # Очистка через TTL или при получении hangup события
    pass
```

#### **Обработка ошибок редактирования:**
```python
async def safe_edit_message(chat_id: int, message_id: int, new_text: str, call_key: str):
    """Безопасное редактирование с обработкой ошибок"""
    try:
        await bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id, 
            text=new_text,
            parse_mode="HTML"
        )
        logger.info(f"✏️ Отредактировано сообщение {message_id} для {call_key}")
        
    except MessageNotModified:
        # Текст не изменился - это нормально
        logger.debug(f"📝 Сообщение {message_id} не требует изменения")
        
    except MessageToEditNotFound:
        # Сообщение удалено пользователем - создаем новое
        logger.warning(f"🔍 Сообщение {message_id} не найдено, создаем новое")
        message = await bot.send_message(chat_id=chat_id, text=new_text, parse_mode="HTML")
        store_call_message_id(call_key, message.message_id)
        
    except Exception as e:
        # Прочие ошибки - создаем новое сообщение
        logger.error(f"❌ Ошибка редактирования {message_id}: {e}")
        message = await bot.send_message(chat_id=chat_id, text=new_text, parse_mode="HTML")
        store_call_message_id(call_key, message.message_id)
```

---

**ВАЖНО:** Этот мануал основан на анализе 42 типов звонков. План будет дополняться по мере реализации и тестирования.

---

## 🗄️ **СТРУКТУРА БАЗЫ ДАННЫХ ДЛЯ КЭШИРОВАНИЯ МЕТАДАННЫХ**

### **📋 АНАЛИЗ ТАБЛИЦ И ЗАВИСИМОСТЕЙ**

#### **🏢 Основная таблица: `enterprises`**
```sql
enterprises (id, number, name, bot_token, chat_id, ip, secret, host, ...)
```
**Описание:** Основная справочная таблица предприятий
- `number` - номер предприятия (например, "0367")
- `name` - название предприятия
- `bot_token` - токен Telegram бота
- `chat_id` - ID чата для уведомлений
- `ip`, `host` - адреса удаленных хостов

#### **📱 GSM линии: `gsm_lines`**
```sql
gsm_lines (id, enterprise_number, line_id, internal_id, phone_number, line_name, goip_id, ...)
```
**Описание:** GSM линии предприятий
- `enterprise_number` → `enterprises.number` (FK)
- `goip_id` → `goip.id` (FK)
- `line_id` - внешний идентификатор линии (приходит в событиях)
- `internal_id` - внутренний идентификатор
- `phone_number` - номер телефона линии
- `line_name` - человекочитаемое название (МТС-1, A1 Главный офис)

#### **🌐 GoIP устройства: `goip`**
```sql
goip (id, enterprise_number, gateway_name, device_ip, device_model, line_count, ...)
```
**Описание:** GoIP шлюзы GSM
- `enterprise_number` → `enterprises.number` (FK)
- `gateway_name` - название шлюза
- `device_ip` - IP адрес устройства
- `line_count` - количество линий на устройстве

#### **☎️ SIP линии: `sip_unit`**
```sql
sip_unit (id, enterprise_number, line_name, password, prefix, provider_id, ...)
```
**Описание:** SIP подключения (интернет-телефония)
- `enterprise_number` → `enterprises.number` (FK)
- `line_name` - название SIP линии
- `provider_id` → `sip.id` (провайдер SIP)

#### **👥 Пользователи: `users`**
```sql
users (id, enterprise_number, email, last_name, first_name, patronymic, personal_phone, follow_me_number, ...)
```
**Описание:** Менеджеры предприятий
- `enterprise_number` → `enterprises.number` (FK)
- `last_name`, `first_name`, `patronymic` - ФИО
- `personal_phone` - мобильный номер
- `follow_me_number` - номер для FollowMe переадресации

#### **📞 Внутренние номера: `user_internal_phones`**
```sql
user_internal_phones (id, user_id, phone_number, password, enterprise_number, ...)
```
**Описание:** Привязка внутренних номеров к пользователям
- `user_id` → `users.id` (FK)
- `enterprise_number` → `enterprises.number` (FK)
- `phone_number` - внутренний номер (150, 151, 152, ...)

#### **🏪 Торговые точки: `shops` + `shop_lines`**
```sql
shops (id, enterprise_number, name, ...)
shop_lines (shop_id, gsm_line_id, enterprise_number)
```
**Описание:** Группировка GSM линий по торговым точкам
- `shops.enterprise_number` → `enterprises.number`
- `shop_lines.shop_id` → `shops.id` (FK)
- `shop_lines.gsm_line_id` → `gsm_lines.id` (FK)

---

### **🔗 СХЕМА ЗАВИСИМОСТЕЙ**

```
enterprises (базовая таблица)
├── users (менеджеры)
│   └── user_internal_phones (внутренние номера)
├── goip (GSM шлюзы)
│   └── gsm_lines (GSM линии)
├── sip_unit (SIP линии)
└── shops (торговые точки)
    └── shop_lines → gsm_lines
```

---

### **📦 СТРУКТУРА КЭША МЕТАДАННЫХ (СЕРВИС 8020)**

#### **🎯 Что нужно кэшировать для отправки в Telegram:**

#### **1. 📱 Справочник GSM/SIP линий:**
```python
line_cache = {
    "0367": {  # enterprise_number
        "0001363": {  # line_id (приходит в событиях)
            "name": "МТС Главный офис",
            "phone": "+375296254070", 
            "operator": "МТС",
            "goip_name": "GoIP-1",
            "shop_name": "Центральный офис"
        }
    }
}
```

#### **2. 👥 Справочник менеджеров:**
```python
manager_cache = {
    "0367": {  # enterprise_number
        "150": {  # internal phone (приходит в событиях)
            "user_id": 123,
            "full_name": "Иванов Иван Иванович",
            "short_name": "Иванов И.И.",
            "personal_phone": "+375296254070",
            "follow_me": 300,
            "department": "Отдел продаж"
        }
    }
}
```

#### **3. 🏢 Справочник предприятий:**
```python
enterprise_cache = {
    "0367": {
        "name": "ООО Рога и Копыта",
        "bot_token": "7280164925:AAHPPXH4Muq07RFMI_J5DyUhZXEo73l7LWI",
        "chat_id": 374573193,
        "host": "10.88.10.xx"
    }
}
```

#### **4. 🔄 Резервные линии:**
```python
backup_lines_cache = {
    "0367": {
        "0001363": ["0001364", "0001365"],  # основная → [резервы]
        "МТС-1": ["МТС-2", "A1-1"]
    }
}
```

---

### **⚡ АЛГОРИТМ ЗАГРУЗКИ В КЭШ**

#### **Полная загрузка метаданных:**
```sql
-- 1. Загрузка GSM линий с названиями GoIP и торговых точек
SELECT 
    gl.enterprise_number,
    gl.line_id,
    gl.internal_id,
    gl.phone_number,
    gl.line_name,
    g.gateway_name as goip_name,
    s.name as shop_name
FROM gsm_lines gl
LEFT JOIN goip g ON gl.goip_id = g.id
LEFT JOIN shop_lines sl ON gl.id = sl.gsm_line_id
LEFT JOIN shops s ON sl.shop_id = s.id
WHERE gl.enterprise_number = %s

-- 2. Загрузка SIP линий
SELECT 
    enterprise_number,
    line_name,
    prefix,
    provider_id
FROM sip_unit
WHERE enterprise_number = %s

-- 3. Загрузка менеджеров с внутренними номерами
SELECT 
    u.enterprise_number,
    uip.phone_number as internal_phone,
    u.id as user_id,
    CONCAT(u.last_name, ' ', u.first_name, ' ', COALESCE(u.patronymic, '')) as full_name,
    CONCAT(u.last_name, ' ', LEFT(u.first_name, 1), '.', LEFT(COALESCE(u.patronymic, ''), 1), '.') as short_name,
    u.personal_phone,
    u.follow_me_number
FROM users u
JOIN user_internal_phones uip ON u.id = uip.user_id
WHERE u.enterprise_number = %s

-- 4. Загрузка данных предприятий
SELECT 
    number,
    name,
    bot_token,
    chat_id,
    host,
    ip
FROM enterprises
WHERE number = %s
```

---

### **🚀 ТЕХНИЧЕСКАЯ РЕАЛИЗАЦИЯ В СЕРВИСЕ 8020**

#### **Класс MetadataCache:**
```python
class MetadataCache:
    def __init__(self, db_connection):
        self.db = db_connection
        self.cache = {}
        self.last_update = {}
        
    async def load_enterprise_metadata(self, enterprise_number: str):
        """Загружает все метаданные предприятия в кэш"""
        
        # Загружаем GSM линии
        self.cache[enterprise_number]["lines"] = await self._load_gsm_lines(enterprise_number)
        
        # Загружаем SIP линии
        self.cache[enterprise_number]["sip_lines"] = await self._load_sip_lines(enterprise_number)
        
        # Загружаем менеджеров
        self.cache[enterprise_number]["managers"] = await self._load_managers(enterprise_number)
        
        # Загружаем данные предприятия
        self.cache[enterprise_number]["enterprise"] = await self._load_enterprise_data(enterprise_number)
        
        self.last_update[enterprise_number] = datetime.now()
        
    def get_line_name(self, enterprise_number: str, line_id: str) -> str:
        """Получает название линии по ID"""
        return self.cache.get(enterprise_number, {}).get("lines", {}).get(line_id, {}).get("name", f"Линия {line_id}")
        
    def get_manager_name(self, enterprise_number: str, internal_phone: str) -> str:
        """Получает ФИО менеджера по внутреннему номеру"""
        return self.cache.get(enterprise_number, {}).get("managers", {}).get(internal_phone, {}).get("full_name", f"Доб.{internal_phone}")
        
    def get_backup_lines(self, enterprise_number: str, primary_line: str) -> List[str]:
        """Получает список резервных линий"""
        return self.cache.get(enterprise_number, {}).get("backup_lines", {}).get(primary_line, [])
```

---

### **📊 ОЦЕНКА ОБЪЕМА ДАННЫХ**

#### **Расчет памяти для кэша:**

**Предприятие среднего размера (например, 0367):**
- **GSM линии:** 50 линий × 200 байт = 10 KB
- **SIP линии:** 20 линий × 150 байт = 3 KB  
- **Менеджеры:** 100 человек × 300 байт = 30 KB
- **Справочники:** 5 KB
- **ИТОГО на предприятие:** ~50 KB

**Для 50 предприятий:** 50 × 50 KB = **2.5 MB**

**Для всей системы (200 предприятий):** 200 × 50 KB = **10 MB**

✅ **ВЫВОД:** При доступных 16 GB RAM и текущем использовании 3-5 GB, кэширование метаданных займет менее 1% памяти - **АБСОЛЮТНО БЕЗОПАСНО**.

---

### **🔄 СТРАТЕГИЯ ОБНОВЛЕНИЯ КЭША**

#### **1. Инициализация при старте сервиса:**
```python
async def startup_cache_initialization():
    """Загружает кэш всех активных предприятий при старте"""
    active_enterprises = await db.fetch_all("SELECT number FROM enterprises WHERE active = true")
    
    for enterprise in active_enterprises:
        await metadata_cache.load_enterprise_metadata(enterprise['number'])
        
    logger.info(f"🗄️ Загружен кэш для {len(active_enterprises)} предприятий")
```

#### **2. Автообновление по расписанию:**
```python
async def scheduled_cache_refresh():
    """Обновление кэша каждые 30 минут"""
    while True:
        await asyncio.sleep(1800)  # 30 минут
        
        for enterprise_number in metadata_cache.cache.keys():
            await metadata_cache.load_enterprise_metadata(enterprise_number)
            
        logger.info("🔄 Кэш метаданных обновлен")
```

#### **3. Реактивное обновление по событиям:**
```python
async def handle_metadata_change_event(event_type: str, enterprise_number: str):
    """Обновляет кэш при изменении данных через админку"""
    if event_type in ["user_updated", "line_updated", "enterprise_updated"]:
        await metadata_cache.load_enterprise_metadata(enterprise_number)
        logger.info(f"🔄 Кэш предприятия {enterprise_number} обновлен по событию {event_type}")
```

---

### **🎯 ИНТЕГРАЦИЯ С ФИЛЬТРАЦИЕЙ TELEGRAM**

#### **Использование кэша при формировании сообщений:**
```python
async def format_telegram_message(event_data: dict, enterprise_number: str) -> str:
    """Форматирует сообщение для Telegram с использованием кэша"""
    
    # Получаем название линии
    line_id = event_data.get("Exten")
    line_name = metadata_cache.get_line_name(enterprise_number, line_id)
    
    # Получаем ФИО менеджера
    internal_phone = event_data.get("Channel", "").split("/")[-1]
    manager_name = metadata_cache.get_manager_name(enterprise_number, internal_phone)
    
    # Форматируем внешний номер
    external_phone = format_phone_display(event_data.get("CallerIDNum", ""))
    
    return f"""
✅ Успешный входящий звонок
💰{external_phone}
☎️{manager_name}
📡{line_name}
⌛ Длительность: {event_data.get('Duration', 'N/A')}
🔉Запись разговора
"""
```

**ИТОГО:** Система кэша метаданных обеспечит быстрое получение человекочитаемых названий и ФИО без запросов к БД при каждом событии.
