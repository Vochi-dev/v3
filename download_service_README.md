# 🔄 Сервис автоматической синхронизации Asterisk

Сервис для автоматической синхронизации данных с удаленных Asterisk серверов в PostgreSQL.

## 🚀 Запуск сервиса

### Управление через скрипт download.sh

```bash
# Запуск сервиса
./download.sh start

# Остановка сервиса  
./download.sh stop

# Перезапуск сервиса
./download.sh restart

# Проверка статуса
./download.sh status
```

### Порт и доступ
- **Порт:** 8007
- **API документация:** http://localhost:8007/docs
- **Статус здоровья:** http://localhost:8007/health

## 📋 API эндпоинты

### 🏠 Основная информация
```http
GET /
```
Возвращает информацию о сервисе и списке предприятий.

### 💚 Проверка здоровья
```http
GET /health
```
Проверяет состояние сервиса и подключение к базе данных.

### 📊 Статус синхронизации
```http
GET /sync/status
```
Показывает статистику синхронизации для всех предприятий.

### 🔄 Синхронизация конкретного предприятия
```http
POST /sync/{enterprise_id}
Content-Type: application/json

{
  "enterprise_id": "0327",
  "force_all": false,
  "date_from": "2025-07-01",
  "date_to": "2025-07-03"
}
```

### 🔄 Синхронизация всех предприятий
```http
POST /sync/all
```

### 🏢 Список предприятий
```http
GET /enterprises
```

## 🛠 Примеры использования

### Синхронизация за конкретный день
```bash
curl -X POST "http://localhost:8007/sync/0327" \
  -H "Content-Type: application/json" \
  -d '{
    "enterprise_id": "0327",
    "date_from": "2025-07-03",
    "date_to": "2025-07-03"
  }'
```

### Синхронизация за диапазон дат
```bash
curl -X POST "http://localhost:8007/sync/0327" \
  -H "Content-Type: application/json" \
  -d '{
    "enterprise_id": "0327",
    "date_from": "2025-07-01",
    "date_to": "2025-07-03"
  }'
```

### Проверка статуса
```bash
curl http://localhost:8007/sync/status | jq
```

### Проверка здоровья
```bash
curl http://localhost:8007/health | jq
```

## ⚙️ Конфигурация

### Настройки предприятий
В файле `download.py` в секции `ENTERPRISE_CONFIGS`:

```python
ENTERPRISE_CONFIGS = {
    "0327": {
        "token": "375296211113",
        "host": "10.88.10.25",
        "ssh_port": "5059",
        "ssh_password": "5atx9Ate@pbx"
    }
    # Добавляйте другие предприятия здесь
}
```

### PostgreSQL подключение
```python
PG_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "database": "postgres", 
    "user": "postgres",
    "password": "r/Yskqh/ZbZuvjb2b3ahfg=="
}
```

## 🔧 Особенности работы

### Логика синхронизации
1. **Получение списка файлов** с удаленного Asterisk сервера
2. **Фильтрация по датам** (если указаны date_from/date_to)
3. **Извлечение событий hangup** из каждого SQLite файла
4. **Парсинг и валидация** данных
5. **Запись в PostgreSQL** с флагом `data_source = 'downloaded'`
6. **Обновление статистики** в таблице `download_sync`

### Предотвращение дублирования
- Использует `ON CONFLICT (unique_id) DO NOTHING` для предотвращения дублей
- Отслеживает активные задачи синхронизации
- Запрещает запуск синхронизации если она уже выполняется

### Обработка ошибок
- Логирование всех операций
- Graceful handling ошибок SSH подключения
- Таймауты для предотвращения зависания
- Rollback транзакций при критических ошибках

## 📈 Мониторинг

### Логи сервиса
Логи выводятся в стандартный вывод с уровнем INFO:
```
2025-07-03 11:39:20,050 - download - INFO - Начинаем синхронизацию предприятия 0327
2025-07-03 11:39:22,773 - download - INFO - Синхронизация предприятия 0327 завершена: обработано 83, новых 3, ошибок 0
```

### Статистика в БД
Таблица `download_sync` содержит:
- Время последней успешной синхронизации
- Количество скачанных событий
- Количество ошибок
- Сообщения об ошибках

### Проверка работоспособности
```bash
# Статус процесса
./download.sh status

# HTTP health check
curl http://localhost:8007/health

# Статистика синхронизации
curl http://localhost:8007/sync/status
```

## 🗂 Структура файлов

```
asterisk-webhook/
├── download.py           # Основной сервис
├── download.sh           # Скрипт управления
├── .uvicorn_download.pid # PID файл (создается автоматически)
└── logs.txt              # Документация по архитектуре БД
```

## 🔄 Интеграция с основной системой

### Различение источников данных
Все данные помечаются полем `data_source = 'downloaded'`, что позволяет:
- Отличать от live событий (`data_source = 'live'`)
- Корректно обрабатывать в аналитике
- Предотвращать отправку старых событий в Telegram

### Связь с таблицами
- **calls** - основные записи звонков
- **call_participants** - нормализованные участники
- **download_sync** - статистика синхронизации

## 🚨 Устранение неполадок

### Проблемы с подключением SSH
```bash
# Проверка SSH подключения вручную
sshpass -p '5atx9Ate@pbx' ssh -p 5059 root@10.88.10.25 'ls /var/log/asterisk/'
```

### Проблемы с базой данных
```bash
# Проверка подключения к PostgreSQL
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT 1;"
```

### Сервис не отвечает
```bash
# Принудительная остановка
./download.sh stop

# Очистка порта вручную
fuser -k 8007/tcp

# Перезапуск
./download.sh start
```

### Проверка логов
```bash
# Просмотр логов в реальном времени
tail -f /var/log/syslog | grep download

# Проверка активных процессов
ps aux | grep uvicorn | grep download
``` 