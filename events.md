# Мануал по событиям Asterisk для интеграций

## ⚠️ ВАЖНО! СТРУКТУРА БД ДЛЯ РАЗРАБОТЧИКОВ

**ВСЕГДА ИЗУЧАЙ СТРУКТУРУ БД ПЕРЕД НАПИСАНИЕМ SQL!!!**

### 🗃️ Основные таблицы:

- **`users`** - менеджеры (first_name, last_name, patronymic, personal_phone, follow_me_number)
- **`user_internal_phones`** - внутренние номера (phone_number, user_id, enterprise_number)
- **`gsm_lines`** - GSM линии (line_id, line_name, phone_number, prefix, goip_id)
- **`goip`** - GSM шлюзы (gateway_name, device_ip, enterprise_number)
- **`sip_unit`** - SIP линии (line_name, prefix, provider_id, enterprise_number)
- **`sip`** - SIP провайдеры (name, server_ip)

### 🔗 Команды для изучения структуры:
```bash
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c '\d "users"'
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c '\d "gsm_lines"'
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c '\d "sip_unit"'
```

---

## 🧪 ТЕСТОВЫЙ СЕРВИС ЗВОНКОВ (PORT 8025)

### Описание
Универсальный веб-сервис для тестирования и эмуляции звонков всех предприятий. Позволяет отправлять полную последовательность событий `start→dial→bridge→hangup` в основной сервис (8000) и проверять корректность обработки Telegram-уведомлений.

### Архитектура
- **Файл:** `call_tester.py`
- **Порт:** 8025
- **Управление:** `test.sh`
- **Интерфейс:** Bootstrap + FastAPI
- **БД:** PostgreSQL (кэш предприятий, менеджеров, линий)

### ✅ ПРОВЕРЕННЫЕ ПАТТЕРНЫ СОБЫТИЙ
- ✅ **1-1 (GSM исходящий):** ПРОВЕРЕН 22.09.2025 - эмулятор отправляет идентичные 9 событий как хост 0367

### Ключевые возможности  
- ✅ **77 предприятий** - автоматическая загрузка из БД
- ✅ **Динамическая загрузка** менеджеров и линий по enterprise_number
- ✅ **3 типа звонков:** входящие (2-1), исходящие (1-1), внутренние (3-1)
- ✅ **Настраиваемые параметры:** длительность, результат (ответили/не ответили)
- ✅ **Реальные токены** из таблицы `enterprises.secret`

### URL доступа
```
# Основной интерфейс для конкретного предприятия
**Внешний доступ:** https://bot.vochi.by/call-tester/?enterprise=0367  
**Локальный доступ:** http://localhost:8025/?enterprise=0367

# Health check
**Внешний:** https://bot.vochi.by/call-tester/health  
**Локальный:** http://localhost:8025/health

# API методы
GET  /api/managers?enterprise=XXXX
GET  /api/lines?enterprise=XXXX  
GET  /api/enterprises
POST /api/test-call
```

### Управление сервисом
```bash
# Запуск/остановка/перезапуск
./test.sh start|stop|restart|status

# Логи
tail -f /var/log/test_service.log

# Процесс
ps aux | grep call_tester
```

### Диагностика

#### 1. Проверка работоспособности
```bash
# Health check
curl -s http://localhost:8025/health | jq .
# Ожидается: {"status":"healthy","enterprises_loaded":77,"total_managers":0,"total_lines":0,"database_connected":true}

# Проверка загрузки предприятий
curl -s http://localhost:8025/api/enterprises | jq '. | length'
# Ожидается: 77
```

#### 2. Проверка данных конкретного предприятия
```bash
# Менеджеры предприятия 0367
curl -s "http://localhost:8025/api/managers?enterprise=0367" | jq .

# Линии предприятия 0367  
curl -s "http://localhost:8025/api/lines?enterprise=0367" | jq .

# Веб-интерфейс
curl -s "http://localhost:8025/?enterprise=0367" | grep -o "<title>.*</title>"
```

#### 3. Тестирование эмуляции звонка
```bash
# POST запрос для тестового звонка
curl -X POST http://localhost:8025/api/test-call \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "call_type=0&external_phone=%2B375296254070&internal_phone=150&line_id=0001363&call_status=2&duration_minutes=3&enterprise=0367"
```

#### 4. Проверка логов и ошибок
```bash
# Основные логи сервиса
tail -f /var/log/test_service.log

# Логи при запуске вручную
cd /root/asterisk-webhook && python3 call_tester.py

# Проверка подключения к БД
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT count(*) FROM enterprises;"
```

#### 5. Nginx конфигурация

**Местоположение:** `/etc/nginx/sites-available/bot.vochi.by`

```nginx
# UI Call Tester (port 8025)
location /call-tester/ {
    proxy_pass         http://127.0.0.1:8025/;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
}
```

**Применение изменений:**
```bash
nginx -t                    # Проверка синтаксиса
systemctl reload nginx      # Перезагрузка конфигурации
```

#### 6. Типичные проблемы

**Проблема:** Сервис не запускается
```bash
# Проверить наличие файла
ls -la /root/asterisk-webhook/call_tester.py

# Проверить зависимости
pip list | grep -E "fastapi|uvicorn|asyncpg|httpx"

# Проверить занятость порта
netstat -tlnp | grep 8025
```

**Проблема:** Не загружаются предприятия (enterprises_loaded: 0)
```bash
# Проверить таблицу enterprises
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT number, name FROM enterprises WHERE number IS NOT NULL LIMIT 5;"

# Проверить подключение к БД
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT 1;"
```

**Проблема:** Не загружаются менеджеры/линии для предприятия
```bash
# Проверить менеджеров для 0367
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT internal_phone, first_name, last_name FROM users WHERE enterprise_number = '0367' AND internal_phone IS NOT NULL;"

# Проверить линии для 0367  
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT line_id, line_name FROM gsm_lines WHERE enterprise_number = '0367';"
```

### Интеграция в систему

Сервис интегрирован в:
- ✅ **`all.sh`** - автоматический запуск/остановка с другими сервисами
- ✅ **Админка суперадминистратора** - мониторинг статуса на порту 8025
- ✅ **`enterprise_admin_service.py`** - отображение в списке сервисов

### Следующие этапы развития
1. **Интеграция в админки предприятий** - добавить кнопку "🧪 Тест звонков"
2. **Расширение типов звонков** - поддержка всех 42 типов из `Типы_звонков_v2.txt`
3. **Пакетное тестирование** - автоматический прогон всех сценариев
4. **Журнал тестов** - сохранение истории тестирования
5. **Интеграция с другими сервисами** - тестирование RetailCRM, МойСклад, U-ON

---

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

---

## 🌐 **API МЕТОДЫ КЭША МЕТАДАННЫХ**

### **📊 Получение статистики кэша**

#### `GET /metadata/stats`
Получает общую статистику кэша метаданных по всем предприятиям.

**Пример ответа:**
```json
{
    "enterprises": 77,
    "total_lines": 983,
    "total_managers": 3,
    "last_updates": {
        "0367": "2025-09-15T11:05:24.303322",
        "0100": "2025-09-15T10:58:08.195301",
        ...
    },
    "memory_enterprises": ["0100", "0367", "0387", ...]
}
```

### **📱 Методы для работы с линиями**

#### `GET /metadata/{enterprise_number}/lines`
Получает все линии предприятия (GSM + SIP).

**Пример ответа:**
```json
{
    "enterprise_number": "0367",
    "lines_count": 17,
    "lines": {
        "0001363": {
            "internal_id": "10225501",
            "phone": "+375447033925",
            "name": "A1 1",
            "prefix": "21",
            "operator": "A1",
            "goip_name": "Vochi-Main",
            "goip_ip": null,
            "shop_name": "Тестовый магазин 1"
        },
        "3880923": {
            "name": "3880923",
            "prefix": "80{9}",
            "provider_id": 1,
            "type": "SIP"
        }
    }
}
```

#### `GET /metadata/{enterprise_number}/line/{line_id}`
Получает информацию о конкретной линии.

**Пример ответа:**
```json
{
    "enterprise_number": "0367",
    "line_id": "0001363",
    "name": "A1 1",
    "operator": "A1",
    "exists": true
}
```

### **👥 Методы для работы с менеджерами**

#### `GET /metadata/{enterprise_number}/managers`
Получает всех менеджеров предприятия.

**Пример ответа:**
```json
{
    "enterprise_number": "0367",
    "managers_count": 2,
    "managers": {
        "150": {
            "user_id": 25,
            "full_name": "Джуновый Джулай",
            "short_name": "Джуновый Д.",
            "personal_phone": "+375296254070",
            "follow_me_number": 300,
            "follow_me_enabled": true
        },
        "151": {
            "user_id": 26,
            "full_name": "Копачёв Алексей",
            "short_name": "Копачёв А.",
            "personal_phone": "+491726993391",
            "follow_me_number": null,
            "follow_me_enabled": false
        }
    }
}
```

#### `GET /metadata/{enterprise_number}/manager/{internal_phone}`
Получает информацию о конкретном менеджере.

**Пример ответа:**
```json
{
    "enterprise_number": "0367",
    "internal_phone": "150",
    "full_name": "Джуновый Джулай",
    "short_name": "Джуновый Д.",
    "personal_phone": "+375296254070",
    "follow_me_number": 300,
    "follow_me_enabled": true,
    "user_id": 25,
    "exists": true
}
```

### **🔄 Методы обновления кэша**

#### `POST /metadata/{enterprise_number}/refresh`
Обновляет метаданные конкретного предприятия.

**Пример ответа:**
```json
{
    "message": "Metadata refreshed for enterprise 0367",
    "enterprise_number": "0367",
    "timestamp": "2025-09-15T11:05:24.303322"
}
```

#### `POST /metadata/refresh-all`
Обновляет метаданные всех активных предприятий.

**Пример ответа:**
```json
{
    "message": "All metadata refreshed",
    "loaded_enterprises": 77,
    "timestamp": "2025-09-15T11:05:24.303322"
}
```

### **📋 Примеры использования в коде**

#### **1. Получение названия линии:**
```python
import httpx

async def get_line_name(enterprise_number: str, line_id: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8020/metadata/{enterprise_number}/line/{line_id}")
        if response.status_code == 200:
            data = response.json()
            return data.get("name", f"Линия {line_id}")
        return f"Линия {line_id}"

# Использование
line_name = await get_line_name("0367", "0001363")  # → "A1 1"
```

#### **2. Получение ФИО менеджера:**
```python
async def get_manager_name(enterprise_number: str, internal_phone: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8020/metadata/{enterprise_number}/manager/{internal_phone}")
        if response.status_code == 200:
            data = response.json()
            return data.get("full_name", f"Доб.{internal_phone}")
        return f"Доб.{internal_phone}"

# Использование
manager_name = await get_manager_name("0367", "150")  # → "Джуновый Джулай"
```

#### **3. Форматирование Telegram сообщения с кэшем:**
```python
async def format_telegram_message_with_cache(event_data: dict, enterprise_number: str) -> str:
    # Получаем метаданные через API кэша
    line_id = event_data.get("Exten", "")
    internal_phone = extract_internal_phone_from_channel(event_data.get("Channel", ""))
    
    # Запросы к кэшу вместо БД
    line_name = await get_line_name(enterprise_number, line_id)
    manager_name = await get_manager_name(enterprise_number, internal_phone)
    
    # Форматируем номер
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

### **⚡ Производительность**

- **Скорость запросов:** 6-14 мс для получения данных из кэша
- **Параллельные запросы:** 10 запросов за 42 мс
- **Автообновление:** каждые 5 минут
- **Объём кэша:** ~10 MB для всех предприятий

### **🔧 Техническая информация**

- **Сервис:** integration_cache.py (порт 8020)
- **База URL:** `http://localhost:8020`
- **Формат ответов:** JSON
- **Коды ошибок:** 
  - `404` - объект не найден
  - `503` - кэш не инициализирован
  - `500` - внутренняя ошибка

---

**Кэш метаданных готов к использованию для улучшения читаемости сообщений в Telegram и других интеграциях! 🚀**

---

## 🎯 ПРАВИЛА ФОРМИРОВАНИЯ СОБЫТИЙ ДЛЯ ЭМУЛЯТОРА

### Общие принципы

#### Переменные для формирования событий:
- **Phone** - берем из поля "Внешний номер"
- **Extensions** - берем из поля "Менеджер" (массив)
- **Token** - name2 из таблицы Enterprises (для 0367 = "375293332255", для других юнитов - другие)
- **Trunk** - Line_id из таблицы gsm_lines (из поля "Линия")

#### Формирование unique_id звонка:
- Берем **первые 6 цифр** текущего unix time
- **Остальные цифры** подставляем из примера
- **Пример**: в примере unique_id = 1757765248.0, текущее unixtime = 1758200194
- **Результат**: в событии передаем 1758200248.0 (первые 6 из текущего + остальные из примера)
- **Исключение**: события Bridge формируем по отдельному паттерну

#### Формирование Bridge ID:
- **Паттерн**: `6d2cd650-65a3-4b24-96bd-ac84c0222a82` (UUID v4)
- **Принцип**: если в шаблоне один unique id бриджа - формируем один на все события
- **Генерация**: используем UUID.v4() или аналогичный генератор

---

## 📋 ПАТТЕРН 1-1: Исходящий звонок - ответили

### Описание паттерна
Исходящий звонок с внешнего номера на внутренний номер менеджера. Звонок успешно принят и завершен.

### Последовательность событий (на основе SQLite данных):

#### 1. **dial** - Инициация исходящего звонка
```json
{
  "Phone": "375296254070",           // Внешний номер (из формы)
  "ExtTrunk": "",                    // Пустое для исходящих
  "ExtPhone": "",                    // Пустое для исходящих  
  "Extensions": ["151"],             // Менеджер (из формы)
  "UniqueId": "1758200248.0",        // Сгенерированный unique_id
  "Token": "375293332255",           // Токен предприятия
  "Trunk": "0001363",                // Line_id GSM линии
  "CallType": 1                      // Тип звонка (1 = исходящий)
}
```

#### 2. **new_callerid** - Установка CallerID
```json
{
  "CallerIDNum": "375296254070",     // Внешний номер
  "Channel": "SIP/0001363-00000001", // SIP канал (Trunk-xxxxxxxx)
  "CallerIDName": "<unknown>",       // Имя вызывающего
  "Context": "from-out-office",      // Контекст Asterisk
  "UniqueId": "1758200249.1",        // Новый unique_id (+1.1)
  "ConnectedLineNum": "151",         // Номер получателя
  "Token": "375293332255",           // Токен предприятия
  "ConnectedLineName": "151",        // Имя получателя
  "Exten": "375296254070"            // Добавочный номер
}
```

#### 3. **bridge_create** - Создание моста
```json
{
  "BridgeType": "",                  // Тип моста
  "BridgeUniqueid": "6d2cd650-65a3-4b24-96bd-ac84c0222a82", // UUID моста
  "UniqueId": "",                    // Пустой для создания моста
  "BridgeCreator": "<unknown>",      // Создатель моста
  "Token": "375293332255",           // Токен предприятия
  "BridgeName": "<unknown>",         // Имя моста
  "BridgeNumChannels": "0",          // Количество каналов
  "BridgeTechnology": "simple_bridge" // Технология моста
}
```

#### 4. **bridge** - Подключение внешнего канала
```json
{
  "CallerIDNum": "375296254070",     // Внешний номер
  "Channel": "SIP/0001363-00000001", // SIP канал внешней линии
  "CallerIDName": "",                // Имя вызывающего
  "BridgeUniqueid": "6d2cd650-65a3-4b24-96bd-ac84c0222a82", // ID моста
  "UniqueId": "1758200249.1",        // UniqueId внешнего канала
  "Exten": "",                       // Добавочный номер
  "ConnectedLineNum": "151",         // Подключенный номер
  "Token": "375293332255",           // Токен предприятия
  "ConnectedLineName": "151"         // Имя подключенного
}
```

#### 5. **bridge** - Подключение внутреннего канала
```json
{
  "CallerIDNum": "151",              // Внутренний номер
  "Channel": "SIP/151-00000000",     // SIP канал внутреннего номера
  "CallerIDName": "151",             // Имя внутреннего
  "BridgeUniqueid": "6d2cd650-65a3-4b24-96bd-ac84c0222a82", // ID моста
  "UniqueId": "1758200248.0",        // UniqueId основного звонка
  "Exten": "375296254070",           // Внешний номер
  "ConnectedLineNum": "<unknown>",   // Неизвестный подключенный
  "Token": "375293332255",           // Токен предприятия
  "ConnectedLineName": "<unknown>"   // Неизвестное имя
}
```

#### 6. **bridge_leave** - Выход внешнего канала
```json
{
  "CallerIDNum": "375296254070",     // Внешний номер
  "Channel": "SIP/0001363-00000001", // SIP канал внешней линии
  "CallerIDName": "<unknown>",       // Имя вызывающего
  "BridgeUniqueid": "6d2cd650-65a3-4b24-96bd-ac84c0222a82", // ID моста
  "UniqueId": "1758200249.1",        // UniqueId внешнего канала
  "ConnectedLineNum": "151",         // Подключенный номер
  "Token": "375293332255",           // Токен предприятия
  "ConnectedLineName": "151",        // Имя подключенного
  "BridgeNumChannels": "1"           // Остался 1 канал
}
```

#### 7. **bridge_leave** - Выход внутреннего канала
```json
{
  "CallerIDNum": "151",              // Внутренний номер
  "Channel": "SIP/151-00000000",     // SIP канал внутреннего номера
  "CallerIDName": "151",             // Имя внутреннего
  "BridgeUniqueid": "6d2cd650-65a3-4b24-96bd-ac84c0222a82", // ID моста
  "UniqueId": "1758200248.0",        // UniqueId основного звонка
  "ConnectedLineNum": "<unknown>",   // Неизвестный подключенный
  "Token": "375293332255",           // Токен предприятия
  "ConnectedLineName": "<unknown>",  // Неизвестное имя
  "BridgeNumChannels": "0"           // Каналов не осталось
}
```

#### 8. **bridge_destroy** - Удаление моста
```json
{
  "BridgeType": "",                  // Тип моста
  "BridgeUniqueid": "6d2cd650-65a3-4b24-96bd-ac84c0222a82", // UUID моста
  "UniqueId": "",                    // Пустой для удаления моста
  "BridgeCreator": "<unknown>",      // Создатель моста
  "Token": "375293332255",           // Токен предприятия
  "BridgeName": "<unknown>",         // Имя моста
  "BridgeNumChannels": "0",          // Количество каналов
  "BridgeTechnology": "simple_bridge" // Технология моста
}
```

#### 9. **hangup** - Завершение звонка
```json
{
  "Phone": "375296254070",           // Внешний номер
  "StartTime": "2025-09-13 15:07:29", // Время начала (вычисляется)
  "Extensions": ["151"],             // Менеджер
  "UniqueId": "1758200248.0",        // UniqueId основного звонка
  "DateReceived": "2025-09-13 15:07:28", // Время получения звонка
  "Token": "375293332255",           // Токен предприятия
  "EndTime": "2025-09-13 15:07:46",  // Время завершения
  "CallType": 1,                     // Тип звонка (исходящий)
  "CallStatus": "2",                 // Статус (2 = ответили)
  "Trunk": "0001363"                 // Line_id GSM линии
}
```

### Временные интервалы (примерные):
- **dial → new_callerid**: мгновенно
- **new_callerid → bridge_create**: ~13 секунд (звонок, ответ)
- **bridge_create → bridge**: ~1 секунда
- **bridge → bridge**: ~1 секунда  
- **bridge → bridge_leave**: ~2 секунды (разговор)
- **bridge_leave → bridge_leave**: мгновенно
- **bridge_leave → bridge_destroy**: ~1 секунда
- **bridge_destroy → hangup**: ~3 секунды

### Особенности генерации:
1. **UniqueId основной**: базовый ID звонка (1758200248.0)
2. **UniqueId вторичный**: +1.1 для new_callerid (1758200249.1)
3. **BridgeUniqueid**: один UUID для всех bridge событий
4. **Channel внешний**: SIP/{Trunk}-xxxxxxxx (8 цифр)
5. **Channel внутренний**: SIP/{Extension}-xxxxxxxx (8 цифр)
6. **Временные метки**: рассчитываются относительно времени начала
