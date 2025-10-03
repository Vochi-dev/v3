# 📊 Call Logger Service - Документация

## 🎯 Назначение
Централизованный сервис для логирования всех событий звонков в структурированном виде с возможностью быстрого поиска и анализа.

## 🔧 Технические характеристики
- **Порт**: 8026
- **Технологии**: FastAPI, Pydantic, PostgreSQL, asyncpg
- **Формат данных**: JSON
- **Логирование**: Структурированное в PostgreSQL с JSONB
- **Партиционирование**: LIST по номерам предприятий

## 📋 Основные функции

### 1. Логирование событий звонков
- **Dial события** - начало исходящих звонков
- **Bridge события** - соединение участников
- **Hangup события** - завершение звонков
- **Другие события** - новые каналы, переводы и т.д.

### 2. Логирование HTTP запросов
- Запросы к сервису метаданных (8020)
- Запросы к внешним API (MoySklad, RetailCRM)
- Время выполнения и статус коды
- Полные данные запросов и ответов

### 3. Логирование SQL операций
- Запросы к PostgreSQL
- Параметры и результаты
- Время выполнения
- Отладочная информация

### 4. Логирование Telegram операций
- Отправка сообщений
- Редактирование сообщений
- Удаление сообщений
- ID сообщений и чатов

## 🌐 API Endpoints

### Основные эндпоинты

#### `GET /`
Главная страница с информацией о сервисе

#### `POST /log/event`
Логирование события звонка
```json
{
  "enterprise_number": "0367",
  "unique_id": "1759490248.0",
  "event_type": "dial",
  "event_data": {
    "Phone": "375296254070",
    "Extensions": ["150"],
    "Trunk": "0001363",
    "CallType": 1
  }
}
```

#### `POST /log/http`
Логирование HTTP запроса
```json
{
  "enterprise_number": "0367",
  "unique_id": "1759490248.0",
  "method": "GET",
  "url": "http://localhost:8020/metadata/0367/manager/150",
  "response_data": {
    "full_name": "Джуновый Джулай",
    "internal_phone": "150"
  },
  "status_code": 200,
  "duration_ms": 245.3
}
```

#### `POST /log/sql`
Логирование SQL запроса
```json
{
  "enterprise_number": "0367",
  "unique_id": "1759490248.0",
  "query": "SELECT raw_data->'ConnectedLineNum' FROM call_events WHERE raw_data->>'Token' = $1",
  "parameters": ["375293332255"],
  "result": {"internal_num": "150"},
  "duration_ms": 12.5
}
```

#### `POST /log/telegram`
Логирование Telegram операции
```json
{
  "enterprise_number": "0367",
  "unique_id": "1759490248.0",
  "chat_id": 7055556176,
  "message_type": "dial",
  "message_id": 13165,
  "message_text": "📞 Исходящий звонок...",
  "action": "send"
}
```

#### `GET /trace/{unique_id}`
Получение полного трейса звонка
```json
{
  "unique_id": "1759490248.0",
  "enterprise_number": "0367",
  "created_at": "2025-10-03T11:29:13",
  "updated_at": "2025-10-03T11:29:47",
  "timeline": [
    {
      "type": "call_event",
      "timestamp": "2025-10-03T11:29:13",
      "data": {
        "event_type": "dial",
        "event_data": {...}
      }
    },
    {
      "type": "http_request",
      "timestamp": "2025-10-03T11:29:14",
      "data": {
        "method": "GET",
        "url": "...",
        "duration_ms": 245.3
      }
    }
  ],
  "summary": {
    "total_events": 3,
    "http_requests": 5,
    "sql_queries": 2,
    "telegram_messages": 2
  }
}
```

#### `GET /search`
Поиск трейсов звонков
```
GET /search?enterprise=0367&phone=375296254070&limit=10
```

#### `GET /health`
Проверка здоровья сервиса

## 🗄️ Дизайн базы данных

### Основная таблица `call_traces`

Упрощенная схема с JSONB для хранения всех событий в одной таблице:

```sql
CREATE TABLE call_traces (
    id BIGSERIAL,
    unique_id VARCHAR(50) NOT NULL,
    enterprise_number VARCHAR(10) NOT NULL,
    phone_number VARCHAR(20),
    call_direction VARCHAR(10),
    call_status VARCHAR(20) DEFAULT 'active',
    start_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    end_time TIMESTAMP WITH TIME ZONE,
    call_events JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
) PARTITION BY LIST (enterprise_number);
```

### Партиционирование

**LIST партиционирование по номерам предприятий** - каждое предприятие в своей партиции:

```sql
-- Партиции с простыми названиями (только 4 цифры)
CREATE TABLE "0367" PARTITION OF call_traces FOR VALUES IN ('0367');
CREATE TABLE "0280" PARTITION OF call_traces FOR VALUES IN ('0280');
CREATE TABLE "0368" PARTITION OF call_traces FOR VALUES IN ('0368');
CREATE TABLE "0286" PARTITION OF call_traces FOR VALUES IN ('0286');
-- И так далее для каждого предприятия...
```

### Индексы

```sql
-- Основные индексы для быстрого поиска
CREATE INDEX idx_call_traces_unique_id ON call_traces (unique_id);
CREATE INDEX idx_call_traces_enterprise ON call_traces (enterprise_number);
CREATE INDEX idx_call_traces_start_time ON call_traces (start_time);
CREATE INDEX idx_call_traces_call_events ON call_traces USING GIN (call_events);
```

### Ограничения

```sql
-- UNIQUE constraint для каждой партиции
ALTER TABLE "0367" ADD CONSTRAINT unique_call_trace_0367 UNIQUE (unique_id, enterprise_number);
ALTER TABLE "0280" ADD CONSTRAINT unique_call_trace_0280 UNIQUE (unique_id, enterprise_number);
-- И так далее...
```

### Функции БД

#### `add_call_event` - Добавление/обновление события

```sql
CREATE OR REPLACE FUNCTION add_call_event(
    p_unique_id VARCHAR(50),
    p_enterprise_number VARCHAR(10),
    p_event_type VARCHAR(30),
    p_event_data JSONB,
    p_phone_number VARCHAR(20) DEFAULT NULL
)
RETURNS BIGINT AS $$
DECLARE
    v_trace_id BIGINT;
    v_call_direction VARCHAR(10);
    v_call_status VARCHAR(20);
BEGIN
    -- Определяем направление и статус звонка
    IF p_event_type IN ('start', 'dial') THEN
        v_call_direction := 'outgoing';
        v_call_status := 'active';
    ELSIF p_event_type = 'hangup' THEN
        v_call_status := 'completed';
    END IF;

    -- Проверяем существующую запись
    SELECT id INTO v_trace_id 
    FROM call_traces 
    WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number;
    
    IF v_trace_id IS NOT NULL THEN
        -- Обновляем существующую запись
        UPDATE call_traces SET
            call_events = call_events || jsonb_build_object(
                'event_sequence', jsonb_array_length(call_events) + 1,
                'event_type', p_event_type,
                'event_timestamp', NOW(),
                'event_data', p_event_data
            ),
            updated_at = NOW()
        WHERE id = v_trace_id;
    ELSE
        -- Создаем новую запись
        INSERT INTO call_traces (unique_id, enterprise_number, phone_number, call_direction, call_status, call_events)
        VALUES (p_unique_id, p_enterprise_number, p_phone_number, v_call_direction, v_call_status, 
                jsonb_build_array(jsonb_build_object(
                    'event_sequence', 1,
                    'event_type', p_event_type,
                    'event_timestamp', NOW(),
                    'event_data', p_event_data
                )))
        RETURNING id INTO v_trace_id;
    END IF;

    RETURN v_trace_id;
END;
$$ LANGUAGE plpgsql;
```

#### `get_call_events` - Извлечение событий из JSONB

```sql
CREATE OR REPLACE FUNCTION get_call_events(p_unique_id VARCHAR(50))
RETURNS TABLE (
    event_sequence INTEGER,
    event_type VARCHAR(30),
    event_timestamp TIMESTAMP WITH TIME ZONE,
    event_data JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        (event->>'event_sequence')::INTEGER,
        (event->>'event_type')::VARCHAR(30),
        (event->>'event_timestamp')::TIMESTAMP WITH TIME ZONE,
        event->'event_data'
    FROM call_traces,
         jsonb_array_elements(call_events) AS event
    WHERE unique_id = p_unique_id
    ORDER BY (event->>'event_sequence')::INTEGER;
END;
$$ LANGUAGE plpgsql;
```

### Преимущества схемы

1. **Простота**: Одна таблица вместо множества связанных
2. **Производительность**: Каждое предприятие в своей партиции
3. **Гибкость**: JSONB позволяет хранить любые структуры событий
4. **Масштабируемость**: Легко добавлять новые партиции
5. **Понятность**: Партиции названы просто - номером предприятия

## 📊 Структура данных

### CallTrace (основная структура в БД)
```json
{
  "id": 123,
  "unique_id": "1759490248.0",
  "enterprise_number": "0367",
  "phone_number": "375296254070",
  "call_direction": "outgoing",
  "call_status": "active",
  "start_time": "2025-10-03T11:29:13",
  "end_time": null,
  "call_events": [
    {
      "event_sequence": 1,
      "event_type": "dial",
      "event_timestamp": "2025-10-03T11:29:13",
      "event_data": {
        "Phone": "375296254070",
        "Extensions": ["150"],
        "Trunk": "0001363"
      }
    }
  ],
  "created_at": "2025-10-03T11:29:13",
  "updated_at": "2025-10-03T11:29:47"
}
```

### Event (событие звонка)
```json
{
  "event_type": "dial|bridge|hangup|new_channel|etc",
  "event_data": {...},       // Данные события
  "timestamp": "2025-10-03T11:29:13"
}
```

### HttpRequest (HTTP запрос)
```json
{
  "method": "GET|POST|PUT|DELETE",
  "url": "http://localhost:8020/...",
  "request_data": {...},     // Данные запроса
  "response_data": {...},    // Данные ответа
  "status_code": 200,
  "duration_ms": 245.3,
  "timestamp": "2025-10-03T11:29:14"
}
```

### SqlQuery (SQL запрос)
```json
{
  "query": "SELECT ...",
  "parameters": [...],       // Параметры запроса
  "result": {...},          // Результат запроса
  "duration_ms": 12.5,
  "timestamp": "2025-10-03T11:29:15"
}
```

### TelegramMessage (Telegram операция)
```json
{
  "chat_id": 7055556176,
  "message_type": "dial|bridge|hangup",
  "message_id": 13165,
  "message_text": "...",
  "action": "send|edit|delete",
  "timestamp": "2025-10-03T11:29:16"
}
```

## 🔄 Жизненный цикл звонка

### 1. Dial событие
```
1. Приходит dial событие от хоста
2. Логируется в /log/event
3. Выполняются HTTP запросы к 8020
4. Логируются в /log/http
5. Отправляется сообщение в Telegram
6. Логируется в /log/telegram
```

### 2. Bridge событие
```
1. Приходит bridge событие
2. Логируется в /log/event
3. Выполняются HTTP запросы для обогащения
4. Логируются в /log/http
5. Отправляется/редактируется сообщение в Telegram
6. Логируется в /log/telegram
```

### 3. Hangup событие
```
1. Приходит hangup событие
2. Логируется в /log/event
3. Выполняются SQL запросы для поиска internal_phone
4. Логируются в /log/sql
5. Выполняются HTTP запросы для обогащения
6. Логируются в /log/http
7. Отправляется финальное сообщение в Telegram
8. Логируется в /log/telegram
9. Удаляются предыдущие сообщения
10. Логируется в /log/telegram
```

## 🎯 Интеграция с основным сервисом

### В dial.py
```python
import httpx

async def log_call_event(enterprise, unique_id, event_type, data):
    async with httpx.AsyncClient() as client:
        await client.post("http://localhost:8026/log/event", json={
            "enterprise_number": enterprise,
            "unique_id": unique_id,
            "event_type": event_type,
            "event_data": data
        })

async def log_http_request(enterprise, unique_id, method, url, response_data, status_code, duration_ms):
    async with httpx.AsyncClient() as client:
        await client.post("http://localhost:8026/log/http", json={
            "enterprise_number": enterprise,
            "unique_id": unique_id,
            "method": method,
            "url": url,
            "response_data": response_data,
            "status_code": status_code,
            "duration_ms": duration_ms
        })
```

## 🔮 План интеграции с основным сервисом

### Этап 1 (База) ✅ ЗАВЕРШЕН
- ✅ Базовый API
- ✅ PostgreSQL с партиционированием
- ✅ 78 партиций для всех предприятий
- ✅ Скрипты управления

### Этап 2 (Интеграция с обработчиками событий) 🔄 В РАБОТЕ
- ✅ **dial.py** - логирование dial событий + HTTP запросов + Telegram (ГОТОВО)
- 🔄 **bridge.py** - логирование bridge событий + обогащение данных
- 🔄 **hangup.py** - логирование hangup + SQL запросы + финальные сообщения

### Этап 3 (Расширенное логирование) 📋 ЗАПЛАНИРОВАНО
- 📋 Логирование всех HTTP запросов к 8020
- 📋 Логирование запросов к внешним API (MoySklad, RetailCRM)
- 📋 Логирование SQL операций
- 📋 Логирование всех Telegram операций

### Этап 4 (Веб-интерфейс) 🔮 БУДУЩЕЕ
- 🔮 HTML страница для просмотра трейсов
- 🔮 Временная шкала событий
- 🔮 Фильтры и поиск
- 🔮 Аналитика и метрики

## 📋 TODO - Статус интеграции

### ✅ ЗАВЕРШЕНО - Интеграция с dial.py
- ✅ Создать утилиту для логирования в основном сервисе
- ✅ Добавить логирование dial события
- ✅ Добавить логирование HTTP запросов к 8020
- ✅ Добавить логирование Telegram сообщений
- ✅ Протестировать полный цикл dial события

### 🐛 ОБНАРУЖЕННЫЕ ОШИБКИ - ИСПРАВИТЬ ЗАВТРА
- [ ] **API /trace/ ошибка**: `column "duration_seconds" does not exist` - исправить функцию get_call_trace в logger.py
- [ ] **Партиция 0233 ошибка**: `invalid bound specification for a list partition` - проверить создание партиции для предприятия 0233
- [ ] **Дублирование dial событий**: записывается 2 одинаковых dial события вместо одного - найти причину дублирования
- [ ] **Обновить документацию**: обновить статус dial.py с 🔄 на ✅ в разделе "Этап 2"

### 🔄 СЛЕДУЮЩИЕ ЗАДАЧИ НА ЗАВТРА:
- [ ] Исправить все обнаруженные ошибки
- [ ] Интеграция с bridge.py
- [ ] Интеграция с hangup.py
- [ ] Добавление логирования SQL запросов
- [ ] Добавление логирования внешних API

### 📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:
- **Статус**: dial.py интеграция работает ✅
- **Трейсов в логгере**: 56 (было 44)
- **Звонков в 0367**: 2 звонка
- **Логируется**: dial события, HTTP запросы, Telegram сообщения, dispatch запросы

## 🚀 Запуск и управление

### Запуск сервиса
```bash
# Запуск
./logger.sh start

# Остановка
./logger.sh stop

# Перезапуск
./logger.sh restart

# Статус
./logger.sh status
```

### Проверка работы
```bash
# Проверка здоровья
curl http://localhost:8026/health

# Получение информации о сервисе
curl http://localhost:8026/
```

## 🗂️ Управление партициями предприятий

### Скрипт `logger_partitions.sh`

Удобный скрипт для управления партициями базы данных по предприятиям с простыми названиями.

#### Основные команды

**Список всех партиций:**
```bash
./logger_partitions.sh list
```
Показывает все созданные партиции с размерами и количеством записей:
```
📋 Список всех партиций:

🗂️ Текущая схема: партиции с простыми названиями по предприятиям
 enterprise_partition | size  | records | enterprise_number 
----------------------+-------+---------+-------------------
 0280                 | 48 kB |       1 | 0280
 0286                 | 48 kB |       0 | 0286
 0367                 | 48 kB |       1 | 0367
 0368                 | 48 kB |       0 | 0368
 0999                 | 48 kB |       1 | 0999
```

**Создание партиции для предприятия:**
```bash
./logger_partitions.sh add-enterprise 0369
```
Простое создание без лишних вопросов:
```
➕ Создание партиции для предприятия 0369...
✅ Партиция 0369 создана успешно!
📝 Теперь можно использовать: SELECT * FROM "0369";
```

**Статистика по предприятию:**
```bash
./logger_partitions.sh stats 0367
```
Показывает детальную статистику:
```
📊 Статистика предприятия 0367:

 total_calls | completed_calls |  avg_events_per_call   | first_call | last_call | events_size 
-------------+-----------------+------------------------+------------+-----------+-------------
           5 |               2 | 2.40000000000000000000 | 2025-10-03 | 2025-10-03| 895 bytes
```

**Очистка данных предприятия:**
```bash
./logger_partitions.sh cleanup 0367     # Очистить данные предприятия
./logger_partitions.sh cleanup-all      # Очистить все данные (с подтверждением)
```

**Пересоздание партиций:**
```bash
./logger_partitions.sh rebuild-partitions
```
Пересоздает партиции с правильными названиями.

**Выполнение SQL запросов:**
```bash
./logger_partitions.sh sql "SELECT COUNT(*) FROM \"0367\";"
./logger_partitions.sh sql "SELECT * FROM \"0367\" LIMIT 5;"
```

**Тестирование логирования:**
```bash
./logger_partitions.sh test-event 0367
```
Создает тестовое событие и показывает результат.

**Проверка здоровья сервиса:**
```bash
./logger_partitions.sh health
```

#### Примеры использования

**Сценарий 1: Новое предприятие**
```bash
# Создаем партицию для нового предприятия 0375
./logger_partitions.sh add-enterprise 0375

# Проверяем что партиция создалась
./logger_partitions.sh list

# Смотрим статистику (пока пустая)
./logger_partitions.sh stats 0375
```

**Сценарий 2: Анализ данных предприятия**
```bash
# Общая статистика по 0367
./logger_partitions.sh stats 0367

# Детальный анализ через SQL (обратите внимание на кавычки!)
./logger_partitions.sh sql "
SELECT 
    call_direction,
    COUNT(*) as calls_count,
    jsonb_array_length(call_events) as avg_events
FROM \"0367\" 
WHERE start_time >= NOW() - INTERVAL '7 days'
GROUP BY call_direction;
"

# Поиск звонков по номеру телефона
./logger_partitions.sh sql "
SELECT unique_id, phone_number, call_status, start_time 
FROM \"0367\" 
WHERE phone_number = '375296254070';
"
```

**Сценарий 3: Очистка тестовых данных**
```bash
# Сначала смотрим что в партиции
./logger_partitions.sh stats 0999

# Если это тестовые данные - очищаем
./logger_partitions.sh cleanup 0999
```

**Сценарий 4: Тестирование логирования**
```bash
# Тестируем логирование для предприятия
./logger_partitions.sh test-event 0367

# Проверяем что событие записалось
./logger_partitions.sh stats 0367

# Смотрим детали события
./logger_partitions.sh sql "SELECT * FROM \"0367\" ORDER BY id DESC LIMIT 1;"
```

#### Автоматическое создание партиций

Партиции **НЕ создаются** автоматически! Нужно создавать их вручную перед логированием:

```bash
# Сначала создаем партицию для нового предприятия
./logger_partitions.sh add-enterprise 0375

# Теперь можно логировать события
curl -X POST http://localhost:8026/log/event -H "Content-Type: application/json" -d '{
    "enterprise_number": "0375",
    "unique_id": "1759490248.0", 
    "event_type": "dial",
    "event_data": {...}
}'
```

#### Важные особенности

**Кавычки в SQL запросах:**
Поскольку партиции названы цифрами, в SQL нужны двойные кавычки:
```sql
-- ✅ Правильно
SELECT * FROM "0367";

-- ❌ Неправильно  
SELECT * FROM 0367;
```

**Простые названия партиций:**
- Партиция для предприятия 0367 называется просто `"0367"`
- Никаких префиксов `call_traces_` больше нет
- Это упрощает понимание и использование

#### Мониторинг партиций

**Ежедневная проверка:**
```bash
# Размеры всех партиций
./logger_partitions.sh list

# Статистика активных предприятий
for enterprise in 0367 0280 0368; do
    echo "=== Предприятие $enterprise ==="
    ./logger_partitions.sh stats $enterprise
    echo ""
done
```

**Поиск проблемных партиций:**
```bash
# Партиции без данных (возможно созданы ошибочно)
./logger_partitions.sh sql "
SELECT 
    tablename as partition_name,
    pg_size_pretty(pg_total_relation_size('public.'||tablename)) as size
FROM pg_tables 
WHERE schemaname = 'public' 
AND tablename ~ '^[0-9]{4}$'
ORDER BY pg_total_relation_size('public.'||tablename) DESC;
"

# Поиск пустых партиций
./logger_partitions.sh sql "
SELECT 
    tablename,
    (SELECT COUNT(*) FROM call_traces WHERE tableoid = ('public.'||tablename)::regclass) as records
FROM pg_tables 
WHERE schemaname = 'public' 
AND tablename ~ '^[0-9]{4}$'
HAVING (SELECT COUNT(*) FROM call_traces WHERE tableoid = ('public.'||tablename)::regclass) = 0;
"
```

## 📈 Мониторинг

### Логи сервиса
- Файл: `logs/logger.log`
- Формат: Стандартный Python logging
- Ротация: По размеру (10MB)

### Метрики
- Количество активных трейсов
- Время обработки запросов
- Ошибки и исключения

## ⚠️ Важные замечания

1. **Производительность**: Сервис не использует --reload для избежания тормозов
2. **Хранилище**: Данные хранятся в PostgreSQL с партиционированием по предприятиям
3. **Безопасность**: Сервис доступен по localhost и через Nginx на `/logger/`
4. **Партиции**: Названы просто номерами предприятий (0367, 0280, etc.) без префиксов
5. **SQL запросы**: Обязательно используйте двойные кавычки для названий партиций
6. **Создание партиций**: Партиции НЕ создаются автоматически, только через скрипт
7. **JSONB**: Все события звонка хранятся в одном JSONB поле для гибкости

## 🔗 Связанные сервисы

- **8000** - Основной сервис (отправляет логи)
- **8020** - Сервис метаданных (источник HTTP запросов)
- **PostgreSQL** - База данных (будущее хранилище)

---

**Версия документации**: 1.0.0  
**Дата обновления**: 2025-10-03  
**Автор**: Call Logger Service Team
