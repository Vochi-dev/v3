# 📊 Call Logger Service - Документация

## 🎯 Назначение
Централизованный сервис для логирования всех событий звонков в структурированном виде с возможностью быстрого поиска и анализа.

## 🔧 Технические характеристики
- **Порт**: 8026
- **Технологии**: FastAPI, Pydantic, PostgreSQL (в будущем)
- **Формат данных**: JSON
- **Логирование**: Структурированное в БД + файловые логи

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

## 📊 Структура данных

### CallTrace (основная структура)
```json
{
  "unique_id": "1759490248.0",
  "enterprise_number": "0367",
  "events": [...],           // События звонка
  "http_requests": [...],    // HTTP запросы
  "sql_queries": [...],      // SQL операции
  "telegram_messages": [...], // Telegram операции
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

## 🔮 Будущие улучшения

### Этап 1 (Текущий - заглушка)
- ✅ Базовый API
- ✅ Временное хранилище в памяти
- ✅ Простой поиск

### Этап 2 (PostgreSQL)
- 🔄 Миграция на PostgreSQL
- 🔄 Индексы для быстрого поиска
- 🔄 Партиционирование по предприятиям

### Этап 3 (Веб-интерфейс)
- 🔄 HTML страница для просмотра трейсов
- 🔄 Временная шкала событий
- 🔄 Фильтры и поиск

### Этап 4 (Продвинутые функции)
- 🔄 Аналитика и метрики
- 🔄 Алерты при ошибках
- 🔄 Экспорт данных
- 🔄 API для внешних систем

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
2. **Хранилище**: В текущей версии данные хранятся в памяти (будут потеряны при перезапуске)
3. **Безопасность**: Сервис доступен только по localhost
4. **Масштабирование**: При росте нагрузки потребуется оптимизация БД

## 🔗 Связанные сервисы

- **8000** - Основной сервис (отправляет логи)
- **8020** - Сервис метаданных (источник HTTP запросов)
- **PostgreSQL** - База данных (будущее хранилище)

---

**Версия документации**: 1.0.0  
**Дата обновления**: 2025-10-03  
**Автор**: Call Logger Service Team
