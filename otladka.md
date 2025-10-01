# 🔧 Руководство по отладке системы событий Asterisk

## 📍 Расположение файлов логов

### **Сервис 8025 (Эмулятор событий)**
- **Основной лог событий:** `/root/asterisk-webhook/logs/call_tester_events.log`
- **Описание:** Детальное логирование всех эмулированных событий
- **Содержимое:**
  - Все отправленные события (dial, bridge_create, bridge, bridge_leave, bridge_destroy, hangup, new_callerid)
  - Полные JSON данные каждого события
  - HTTP заголовки запросов и ответов
  - Статусы ответов от сервера bot.vochi.by
  - Серверные ошибки и их детали
  - Временные метки отправки и получения

**Команды для работы:**
```bash
# Просмотр всех логов эмулятора
cat logs/call_tester_events.log

# Последние события
tail -n 100 logs/call_tester_events.log

# Мониторинг в реальном времени
tail -f logs/call_tester_events.log

# Очистка лога перед тестированием
cat /dev/null > logs/call_tester_events.log

# Поиск конкретного события
grep -A 10 "🚀 EMULATION" logs/call_tester_events.log

# Поиск ошибок
grep "ERROR" logs/call_tester_events.log
```

---

### **Сервис 8000 (Основной webhook-сервер) - Предприятие 0367/june**
- **Специальный лог для 0367:** `/root/asterisk-webhook/logs/0367.log`
- **Token:** `375293332255`
- **Описание:** Изолированное логирование событий только от тестового предприятия 0367 (june)
- **Содержимое:**
  - Тип события (start, dial, bridge, hangup и т.д.)
  - Token и UniqueId
  - Полное тело события в JSON формате
  - Эмодзи-маркеры для быстрого визуального поиска

**Команды для работы:**
```bash
# Просмотр всех событий 0367
cat logs/0367.log

# Последние события
tail -n 50 logs/0367.log

# Мониторинг в реальном времени
tail -f logs/0367.log

# Очистка лога перед тестированием
cat /dev/null > logs/0367.log

# Поиск по типу события
grep "hangup" logs/0367.log
grep "bridge" logs/0367.log

# Поиск по UniqueId
grep "1759225684.16" logs/0367.log

# Подсчет событий каждого типа
grep "🧪 TEST EVENT:" logs/0367.log | awk '{print $NF}' | sort | uniq -c
```

---

### **Сервис 8000 (Основной webhook-сервер) - Все предприятия**
- **Основной лог:** `/root/asterisk-webhook/logs/app.log`
- **Uvicorn лог:** `/root/asterisk-webhook/logs/uvicorn.log`
- **Лог доступа:** `/root/asterisk-webhook/logs/access.log`
- **Описание:** Общие логи от всех предприятий (сотни хостов)
- **Формат:** `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- **Ротация:** 10MB на файл, 5 бэкапов

**Команды для работы:**
```bash
# Просмотр основного лога
tail -f logs/app.log

# Фильтрация по Token 0367
grep "375293332255" logs/app.log

# Поиск ошибок
grep "ERROR" logs/app.log
```

---

## 🧪 Типичный процесс отладки

### **Шаг 1: Подготовка**
```bash
# Очищаем логи эмулятора и 0367
cat /dev/null > logs/call_tester_events.log
cat /dev/null > logs/0367.log
```

### **Шаг 2: Эмуляция события**
- Открыть интерфейс: https://bot.vochi.by/test-interface
- Выбрать нужный паттерн
- Заполнить форму
- Нажать "Отправить события"

### **Шаг 3: Проверка отправки (сервис 8025)**
```bash
# Проверяем что события отправились
tail -50 logs/call_tester_events.log | grep "📥 EMULATION RESPONSE"
```

### **Шаг 4: Проверка получения (сервис 8000)**
```bash
# Проверяем что события получены на основном сервере
tail -50 logs/0367.log
```

### **Шаг 5: Анализ**
- Сравнить отправленные и полученные события
- Проверить корректность данных
- Выявить расхождения

---

## 📊 Структура логов 0367.log

**Пример записи:**
```
2025-10-01 14:23:45,123 [INFO] 🧪 TEST EVENT: hangup
2025-10-01 14:23:45,124 [INFO] 📋 Token: 375293332255, UniqueId: 1759230684.16
2025-10-01 14:23:45,125 [INFO] 📦 Full Body: {
  "Token": "375293332255",
  "CallStatus": "2",
  "Phone": "152",
  "ExternalInitiated": true,
  "Trunk": "",
  "Extensions": ["150"],
  "UniqueId": "1759230684.16",
  "StartTime": "",
  "DateReceived": "2025-09-30 11:15:02",
  "EndTime": "2025-09-30 11:15:12",
  "CallType": 0
}
```

---

## 🔄 Перезапуск сервисов

**❌ НЕ ИСПОЛЬЗОВАТЬ:**
```bash
./all.sh restart  # Может привести к проблемам с запуском отдельных сервисов
```

**✅ ПРАВИЛЬНО:**
```bash
# Перезапуск только основного сервиса 8000
./main.sh restart

# Перезапуск эмулятора 8025
./call_tester.sh restart
```

**⚠️ ВАЖНО:** Если требуется перезапуск всех сервисов - сообщить пользователю, он выполнит `./all.sh restart` вручную.

---

## 🎯 Быстрые команды для работы

```bash
# Одновременный мониторинг эмулятора и основного сервера
tail -f logs/call_tester_events.log logs/0367.log

# Очистка обоих логов
cat /dev/null > logs/call_tester_events.log && cat /dev/null > logs/0367.log && echo "✅ Логи очищены"

# Подсчет событий в текущей сессии
echo "События от эмулятора:" && grep -c "📥 EMULATION RESPONSE" logs/call_tester_events.log
echo "События на сервере 0367:" && grep -c "🧪 TEST EVENT" logs/0367.log

# Быстрая проверка последнего события
echo "=== Последнее отправленное ===" && grep "📊 Event Data:" logs/call_tester_events.log | tail -1
echo "=== Последнее полученное ===" && grep "🧪 TEST EVENT" logs/0367.log | tail -1
```

---

---

## 🔄 Архитектура обработки событий

### **Общая схема потока данных:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ВХОДЯЩЕЕ СОБЫТИЕ ОТ ASTERISK                         │
│                      (хост 10.88.10.xxx или эмулятор)                       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │     main.py (порт 8000)      │
                    │  POST /start, /dial, /bridge │
                    │  /hangup, /bridge_create и т.д│
                    └──────────┬───────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌─────────────────┐ ┌────────────┐ ┌──────────────────┐
    │ 1. Логирование  │ │ 2. СУБД    │ │ 3. Нормализация  │
    │ logs/0367.log   │ │ PostgreSQL │ │ входящих номеров │
    │ logs/app.log    │ │call_events │ │ (правила на линии)│
    └─────────────────┘ └────────────┘ └──────────────────┘
                               │
                               ▼
                    ┌──────────────────────────────┐
                    │   _dispatch_to_all(handler)  │
                    │ - Находит bot_token по Token │
                    │ - Получает список tg_ids     │
                    │ - Вызывает handler для каждого│
                    └──────────┬───────────────────┘
                               │
              ┌────────────────┼────────────────┬─────────────────┐
              ▼                ▼                ▼                 ▼
    ┌─────────────────┐ ┌────────────┐ ┌──────────────┐ ┌──────────────┐
    │  TELEGRAM BOT   │ │ Сервис     │ │ RetailCRM    │ │ Собственная  │
    │ app/services/   │ │ 8020       │ │ Сервис 8019  │ │ CRM (TODO)   │
    │ calls/*         │ │Integration │ │              │ │              │
    │                 │ │ Gateway    │ │              │ │              │
    └─────────────────┘ └────────────┘ └──────────────┘ └──────────────┘
```

---

## 📨 Отправка событий в Telegram

### **Точка входа: `main.py`**

**Эндпоинты для событий Asterisk:**
- `POST /start` → `_dispatch_to_all(process_start, body)`
- `POST /dial` → `_dispatch_to_all(process_dial, body)`
- `POST /bridge` → `_dispatch_to_all(process_bridge, body)`
- `POST /hangup` → `_dispatch_to_all(process_hangup, body)`
- `POST /bridge_create` → `_dispatch_to_all(process_bridge_create, body)`
- `POST /bridge_leave` → `_dispatch_to_all(process_bridge_leave, body)`
- `POST /bridge_destroy` → `_dispatch_to_all(process_bridge_destroy, body)`
- `POST /new_callerid` → `_dispatch_to_all(process_new_callerid, body)`

**Расположение:** `/root/asterisk-webhook/main.py`
- Строки 434-517: Определение эндпоинтов

### **Функция `_dispatch_to_all` (main.py, строка 651)**

**Назначение:** Универсальный диспетчер событий

**Алгоритм:**
1. Извлекает `Token` и `UniqueId` из тела события
2. Определяет тип события (start, dial, bridge, hangup и т.д.)
3. **Логирование 0367:** Если `Token == "375293332255"` → пишет в `logs/0367.log`
4. Применяет нормализацию номеров (если настроена для предприятия)
5. Сохраняет событие в PostgreSQL (`call_events` таблица)
6. Находит `bot_token` по `Token` из таблицы `enterprises`
7. Получает список `tg_ids` из таблицы `telegram_users`
8. Для каждого `chat_id` вызывает соответствующий `handler`
9. Для `hangup` генерирует общий UUID токен для всех получателей
10. Отмечает успешную отправку в БД (`telegram_sent = true`)

**Расположение:** `/root/asterisk-webhook/main.py:651-745`

---

## 🤖 Обработчики событий Telegram

### **Директория:** `/root/asterisk-webhook/app/services/calls/`

### **Основные обработчики (текущая версия):**

#### **1. process_start** (`start.py`)
- **Назначение:** Обработка события начала звонка
- **Текст сообщения:** `"📞 Звонок от {номер} → {добавочный}"`
- **Логика:**
  - Группировка событий по номеру телефона
  - Определение: заменить предыдущее сообщение или отправить комментарий
  - Сохранение в `asterisk_logs`
  - Обновление кэша `bridge_store` и `phone_message_tracker`
- **Расположение:** `/root/asterisk-webhook/app/services/calls/start.py`

#### **2. process_dial** (`dial.py`)
- **Назначение:** Обработка события набора номера (дозвон)
- **Текст сообщения:** `"📞 Дозвон от {номер} → {добавочные}"`
- **Логика:**
  - Заменяет или комментирует предыдущее `start` сообщение
  - Поддержка множественных Extensions (перечисление через запятую)
  - Обогащение метаданными (ФИО клиента, ФИО менеджера)
  - **Fire-and-forget отправка в Integration Gateway (8020)**
  - Сохранение в `asterisk_logs`
- **Отправка в 8020:**
  - URL: `http://localhost:8020/dispatch/call-event`
  - Payload: `{ token, uniqueId, event_type: "dial", raw: {...} }`
  - Таймаут: 2 секунды
  - Строки 203-237
- **Расположение:** `/root/asterisk-webhook/app/services/calls/dial.py`

#### **3. process_bridge** (`bridge.py`)
- **Назначение:** Обработка события соединения абонентов
- **Текст сообщения:** `"🔗 Разговор: {номер} ↔ {добавочный}"`
- **Логика:**
  - Проверка: нужно ли отправлять данный bridge (фильтрация дублей)
  - Мгновенная отправка (БЕЗ кэширования и ожидания 5 секунд)
  - Определение правильного bridge для отправки
  - Обновление кэша `bridge_store` и `active_bridges`
  - Сохранение в `asterisk_logs`
- **Дополнительные функции в bridge.py:**
  - `process_bridge_create` - создание моста
  - `process_bridge_leave` - выход из моста
  - `process_bridge_destroy` - разрушение моста
  - `process_new_callerid` - обновление CallerID
- **Расположение:** `/root/asterisk-webhook/app/services/calls/bridge.py`

#### **4. process_hangup** (`hangup.py`)
- **Назначение:** Обработка завершения звонка
- **Текст сообщения:** 
  - `"✅ Завершен: {номер} ↔ {добавочный}, {длительность}"`
  - `"❌ Не отвечен: {номер} → {добавочный}"`
- **Логика:**
  - Определение статуса: ответили (CallStatus="2") или нет
  - Расчет длительности звонка
  - Создание записи в таблице `calls` (PostgreSQL)
  - Обновление агрегатов клиентов (`customers`)
  - **Fire-and-forget отправка в Integration Gateway (8020)**
  - Генерация `uuid_token` для связи с записью разговора
  - Поддержка множественных Extensions
  - Сохранение в `asterisk_logs` и `telegram_messages`
- **Отправка в 8020:**
  - URL: `http://localhost:8020/dispatch/call-event`
  - Payload: `{ token, uniqueId, event_type: "hangup", raw: {...} }`
  - Таймаут: 2 секунды
- **Расположение:** `/root/asterisk-webhook/app/services/calls/hangup.py`

### **Внутренние звонки (internal.py):**

#### **5. process_internal_start** (`internal.py`)
- **Текст:** `"🛎️ Внутренний звонок\n{caller} → {callee}"`
- **Особенности:** Обогащение ФИО из метаданных

#### **6. process_internal_bridge** (`internal.py`)
- **Текст:** `"🔗 Разговор (внутр.): {caller} ↔ {callee}"`

#### **7. process_internal_hangup** (`internal.py`)
- **Текст:** `"✅ Завершен (внутр.): {caller} ↔ {callee}, {длительность}"`
- **Особенности:** 
  - Создает запись в таблице `calls` с `CallType=2`
  - Не отправляет в Integration Gateway (внутренние звонки)

**Расположение:** `/root/asterisk-webhook/app/services/calls/internal.py`

---

## 🔧 Вспомогательные модули

### **utils.py** - Утилиты для обработчиков
- `format_phone_number()` - форматирование номеров
- `is_internal_number()` - проверка внутреннего номера
- `get_phone_for_grouping()` - определение номера для группировки событий
- `should_send_as_comment()` - логика: комментарий или новое сообщение
- `should_replace_previous_message()` - логика замены предыдущего сообщения
- In-memory кэши: `dial_cache`, `bridge_store`, `active_bridges`, `phone_message_tracker`
- **Расположение:** `/root/asterisk-webhook/app/services/calls/utils.py`

### **Версия v2 (альтернативные обработчики):**
- `start_v2.py` - упрощенная версия start
- `dial_v2.py` - упрощенная версия dial
- `bridge_v2.py` - упрощенная версия bridge (правило: "один bridge на звонок")
- `hangup_v2.py` - упрощенная версия hangup

**Статус:** Используются только основные версии (без v2), v2 - экспериментальные

---

## 🔌 Отправка в Integration Gateway (8020)

### **Назначение:**
Централизованная точка для отправки событий во внешние интеграции (RetailCRM, МойСклад, U-ON и т.д.)

### **Сервис:** `integration_cache.py` (порт 8020)

### **Эндпоинт:** `POST /dispatch/call-event`

**Payload:**
```json
{
  "token": "375293332255",
  "uniqueId": "1759310248.0",
  "event_type": "dial" | "hangup",
  "raw": { /* полное тело события от Asterisk */ }
}
```

### **Алгоритм работы 8020:**
1. Находит `enterprise_number` по `token` в БД
2. Проверяет активные интеграции из in-memory кэша (TTL ~90 сек)
3. Для `retailcrm: true` → отправляет в сервис 8019
4. Для `hangup` без предшествующего `dial` → создает синтетический dial
5. Логирует в `integration_logs` таблицу

### **Откуда отправляется:**
- **dial.py** (строки 203-237): после отправки в Telegram, fire-and-forget
- **hangup.py**: после создания записи в `calls`, fire-and-forget
- **Таймаут:** 2 секунды
- **Режим:** `asyncio.create_task()` - не блокирует ответ Asterisk

**Расположение сервиса:** `/root/asterisk-webhook/integration_cache.py`

---

## 🛒 RetailCRM интеграция (8019)

### **Сервис:** `retailcrm.py` (порт 8019)

### **Эндпоинт:** `POST /internal/retailcrm/call-event`

**Прием только с localhost** (защита от внешних запросов)

### **Логика:**
1. Преобразует payload на основе `raw` данных
2. Отправляет в RetailCRM API:
   - **Реалтайм:** `POST /api/v5/telephony/call/event`
   - **Персистентно (hangup):** `POST /api/v5/telephony/calls/upload`
3. Ретраи: 2-3 попытки
4. Идемпотентность по `(enterprise, callExternalId, type)`
5. Логирование в `integration_logs`

**Расположение сервиса:** `/root/asterisk-webhook/retailcrm.py`

---

## 📊 База данных (PostgreSQL)

### **Таблица `call_events`** - События от Asterisk
- `unique_id` - UniqueId звонка
- `event_type` - тип события (start, dial, bridge, hangup)
- `event_timestamp` - время получения
- `data_source` - источник ('live', 'recovery')
- `raw_data` - JSON с полным телом события
- `telegram_sent` - флаг успешной отправки в Telegram

**Сервис записи:** `app/services/events.py` → `save_asterisk_event()`

### **Таблица `calls`** - Завершенные звонки
- `uuid` - уникальный токен звонка
- `unique_id` - UniqueId от Asterisk
- `enterprise_number` - номер предприятия
- `phone` - внешний номер
- `extension` - внутренний добавочный
- `call_type` - тип (0=внешний, 1=исходящий, 2=внутренний)
- `call_status` - статус (0=не ответили, 2=ответили)
- `date_received` - время начала
- `date_end` - время завершения
- `duration` - длительность в секундах
- `record_url` - ссылка на запись (если есть)

**Сервис записи:** `app/services/calls/hangup.py` → `create_call_record()`

### **Таблица `enterprises`** - Предприятия
- `name` - номер предприятия (0367, 0270, и т.д.)
- `name2` - Token для Asterisk (375293332255 для 0367/june)
- `bot_token` - токен Telegram бота
- `integrations_config` - JSON с настройками интеграций

### **Таблица `telegram_users`** - Подписчики
- `tg_id` - Telegram chat_id
- `bot_token` - к какому боту привязан
- `enterprise_number` - номер предприятия

---

## 🎯 План отладки Telegram-отправки

### **Фаза 1: Анализ текущего состояния** ✅
- ✅ Изучена архитектура потока данных
- ✅ Найдены все обработчики событий
- ✅ Определены точки логирования
- ✅ Создан файл otladka.md

### **Фаза 2: Тестирование отправки в Telegram** (TODO)
**Цель:** Проверить целостность и формат событий, отправляемых в Telegram

**Задачи:**
1. Эмулировать полный цикл звонка (start → dial → bridge → hangup)
2. Проверить логи `0367.log` на наличие всех событий
3. Проверить сообщения в Telegram боте
4. Сравнить отправленные данные с ожидаемым форматом
5. Выявить расхождения и ошибки

**Типичные проблемы:**
- Дублирование сообщений (несколько bridge)
- Отсутствие обновлений предыдущих сообщений
- Неправильная группировка событий по номеру
- Отсутствие метаданных (ФИО клиента/менеджера)
- Неправильный формат времени/длительности
- Отсутствие записей разговоров

### **Фаза 3: Исправление выявленных проблем** (TODO)

### **Фаза 4: Отладка отправки в Integration Gateway (8020)** (TODO)

### **Фаза 5: Отладка отправки в собственную CRM** (TODO)

---

---

## 🔗 Восстановление связи 8000 ↔ 8020

### **Проблема:**
В процессе тестирования была отключена связь между основным сервисом 8000 и Integration Cache 8020. Сервис 8020 держит в памяти критически важные данные для обогащения событий в Telegram:
- Конфигурации интеграций предприятий
- Имена клиентов из CRM
- Профили клиентов
- Метаданные менеджеров
- Названия линий и операторов

### **Диагностика (2025-10-01 09:13):**

**Статистика 8020:**
```json
{
  "hits": 0,
  "misses": 696,
  "cache_size": 0,           // ❌ КРИТИЧНО: Пустой кэш статусов
  "config_cache_size": 1,    // ✅ OK: Есть 1 конфиг
  "hit_rate_percent": 0.0,   // ❌ КРИТИЧНО: 0% попаданий
  "last_full_refresh": "2025-10-01T09:36:31"
}
```

**Проверка endpoints:**
```bash
# ✅ Работает для number=0367
curl http://127.0.0.1:8020/integrations/0367
→ {"integrations": {"ms": true, "uon": true, "retailcrm": true, ...}}

# ❌ Не работает для name=june
curl http://127.0.0.1:8020/integrations/june  
→ {"detail": "Enterprise not found"}

# ⚠️ Работает, но данных нет
curl http://127.0.0.1:8020/customer-name/0367/37052628760
→ {"name": null}
```

**Проблемы:**
1. **Кэш статусов пустой** (`cache_size: 0`) - нет данных для быстрого доступа
2. **100% промахов** - все запросы идут в БД, нет benefit от кэширования
3. **Поиск по name не работает** - сервис ищет только по `number`, а не по `name`
4. **Нет данных клиентов** - не настроены primary интеграции или нет данных в CRM

### **Архитектура взаимодействия:**

```
┌──────────────────────────────────────────────────────────────┐
│                    main.py (8000)                            │
│  - process_dial, process_bridge, process_hangup             │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      │ httpx.AsyncClient
                      ▼
         ┌────────────────────────────────┐
         │  metadata_client.py            │
         │  - get_customer_name()         │
         │  - get_customer_profile()      │
         │  - get_manager_name()          │
         │  - enrich_message_data()       │
         └────────┬───────────────────────┘
                  │
                  │ GET http://localhost:8020/...
                  ▼
    ┌──────────────────────────────────────────┐
    │   integration_cache.py (8020)            │
    │                                          │
    │  Endpoints:                              │
    │  - /integrations/{enterprise_number}     │
    │  - /config/{enterprise_number}           │
    │  - /customer-name/{ent}/{phone}          │
    │  - /customer-profile/{ent}/{phone}       │
    │  - /metadata/{ent}/manager/{ext}         │
    │  - /metadata/{ent}/line/{line_id}        │
    └──────────┬───────────────────────────────┘
               │
               │ 1. Check in-memory cache
               │ 2. If miss → PostgreSQL
               │ 3. If still no data → call CRM services
               │
      ┌────────┼────────┬──────────┬─────────┐
      ▼        ▼        ▼          ▼         ▼
   ┌────┐  ┌────┐  ┌──────┐  ┌──────┐  ┌────────┐
   │ DB │  │8019│  │ 8023 │  │ 8022 │  │  8024  │
   │    │  │RCR │  │  MS  │  │ UON  │  │Bitrix  │
   └────┘  └────┘  └──────┘  └──────┘  └────────┘
```

### **Решение:**

#### **Шаг 1: Проверка работы сервиса 8020**
```bash
# Проверить что сервис запущен
./integration_cache.sh status

# Проверить логи
tail -50 logs/integration_cache.log | grep -E "(ERROR|WARNING|📊|🔄)"

# Проверить статистику
curl http://127.0.0.1:8020/stats | jq
```

#### **Шаг 2: Принудительное обновление кэша**
```bash
# Обновить весь кэш из БД
curl -X POST http://127.0.0.1:8020/cache/refresh

# Проверить результат
curl http://127.0.0.1:8020/stats | jq '.cache_size, .hit_rate_percent'
```

#### **Шаг 3: Проверка работы обогащения**
```bash
# Проверить получение имени клиента
curl http://127.0.0.1:8020/customer-name/0367/375296254070

# Проверить профиль клиента  
curl http://127.0.0.1:8020/customer-profile/0367/375296254070

# Проверить метаданные менеджера
curl http://127.0.0.1:8020/metadata/0367/manager/151
```

#### **Шаг 4: Настройка primary интеграции (если нет данных)**
```sql
-- Проверить текущую конфигурацию
SELECT number, integrations_config->'smart' as smart_config 
FROM enterprises 
WHERE number = '0367';

-- Если нет primary интеграции, установить
UPDATE enterprises 
SET integrations_config = jsonb_set(
    COALESCE(integrations_config, '{}'::jsonb),
    '{smart,primary}',
    '"retailcrm"'
)
WHERE number = '0367';

-- Инвалидировать кэш после изменения
curl -X POST http://127.0.0.1:8020/cache/invalidate/0367
```

#### **Шаг 5: Тестирование полного цикла**
```bash
# 1. Очистить логи
cat /dev/null > logs/0367.log

# 2. Отправить событие через эмулятор (dial с известным номером)

# 3. Проверить что обогащение сработало
grep "customer_name\|full_name" logs/0367.log

# 4. Проверить сообщение в Telegram - должно быть ФИО клиента
```

### **Контрольные точки:**

✅ **Сервис 8020 работает** - `./integration_cache.sh status`  
✅ **Кэш не пустой** - `cache_size > 0`  
✅ **Hit rate > 50%** - эффективность кэширования  
✅ **Endpoint /integrations/0367 работает** - возвращает integrations  
✅ **Endpoint /customer-name работает** - возвращает имя или null  
✅ **Обогащение в Telegram** - сообщения содержат ФИО клиентов  

### **Известные ограничения:**

1. **Поиск только по number** - сервис 8020 не ищет по `name` (june), только по `number` (0367)
2. **TTL кэша 90 сек** - данные автоматически протухают через 1.5 минуты
3. **Автообновление 4-5 мин** - полный refresh кэша каждые 4-5 минут
4. **Нет fallback для имен** - если в CRM нет клиента, возвращается `null`

### **Мониторинг здоровья связи 8000 ↔ 8020:**

```bash
# Скрипт для периодической проверки
watch -n 30 'curl -s http://127.0.0.1:8020/stats | jq "{cache_size, hit_rate_percent, config_cache_size, last_refresh: .last_full_refresh}"'

# Алерт если кэш пустой
if [ $(curl -s http://127.0.0.1:8020/stats | jq '.cache_size') -eq 0 ]; then
    echo "❌ ALERT: Integration cache is empty!"
    curl -X POST http://127.0.0.1:8020/cache/refresh
fi
```

---

## 📝 История изменений

### 2025-10-01 - Создание системы отладки
- ✅ Создан отдельный лог для предприятия 0367 (`logs/0367.log`)
- ✅ Настроено параллельное логирование событий от Token `375293332255`
- ✅ Добавлены эмодзи-маркеры для визуального поиска
- ✅ Создан файл `otladka.md` с руководством по отладке
- ✅ Изучена и задокументирована архитектура обработки событий
- ✅ Найдены и описаны все обработчики Telegram
- ✅ Описаны точки интеграции с сервисом 8020 и RetailCRM 8019
- ✅ Диагностирована проблема со связью 8000 ↔ 8020
- ✅ Разработан план восстановления связи и обогащения данных

### 2025-10-01 - Исправление обогащения метаданными
- ✅ **НАЙДЕНА КРИТИЧЕСКАЯ ОШИБКА:** `enterprise_number = token[:4]` давало `"3752"` вместо `"0367"`
- ✅ Исправлен файл `app/services/calls/dial.py`: добавлен запрос к БД `SELECT number FROM enterprises WHERE name2 = $1`
- ✅ Очищен debug.log (был 510MB, логирование app.log не работало)
- 🔄 Ожидается перезапуск сервисов и тестирование обогащения


