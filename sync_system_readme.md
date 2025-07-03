# Система синхронизации Asterisk событий

## Обзор

Система обеспечивает автоматическую синхронизацию событий звонков между удаленными Asterisk серверами и центральной базой данных PostgreSQL.

## Архитектура

### Источники данных

1. **APIlogs** (таблица в SQLite на удаленных серверах)
   - Хранит события, отправленные на старый webhook сервер
   - Статус: `ok` (успешно отправлено)
   - Используется для массового импорта исторических данных

2. **AlternativeAPIlogs** (таблица в SQLite на удаленных серверах)  
   - Хранит события для отправки на новый webhook сервер
   - Статус: `NULL` или `!= 'ok'` (не отправлено, так как пока отключено)
   - Используется для live синхронизации текущих событий

### Центральная база данных (PostgreSQL)

#### Таблица `calls`
- **data_source**: 
  - `'downloaded'` - данные из APIlogs (исторические)
  - `'live'` - данные из AlternativeAPIlogs (текущие)
  - `'migrated'` - будущие данные после миграции

#### Остальные таблицы
- `call_participants` - участники звонков
- `call_events` - промежуточные события (Start, Dial, Bridge)
- `download_sync` - статистика синхронизации

## Сервис синхронизации (download.py)

### Запуск
```bash
cd /root/asterisk-webhook
python3 download.py
```
Работает на порту **8007**

### Автоматическая синхронизация
- Каждые **5 минут** автоматически сканирует все предприятия
- Ищет события в `AlternativeAPIlogs` со статусом НЕ ok
- Обрабатывает только файл текущей даты: `Listen_AMI_YYYY-MM-DD.db`

### API эндпоинты

#### Информация о сервисе
```bash
curl http://localhost:8007/
curl http://localhost:8007/health
```

#### Live синхронизация (AlternativeAPIlogs)
```bash
# Для конкретного предприятия
curl -X POST http://localhost:8007/sync/live/0327

# Для всех предприятий
curl -X POST http://localhost:8007/sync/live/all

# Статистика live событий
curl http://localhost:8007/sync/live/status
```

#### Исторические данные (APIlogs)
```bash
# Для конкретного предприятия с опциями
curl -X POST http://localhost:8007/sync/0327 -H "Content-Type: application/json" -d '{
  "enterprise_id": "0327",
  "force_all": false,
  "date_from": "2025-06-22",
  "date_to": "2025-07-03"
}'

# Для всех предприятий
curl -X POST http://localhost:8007/sync/all

# Общий статус синхронизации
curl http://localhost:8007/sync/status
```

#### Список предприятий
```bash
curl http://localhost:8007/enterprises
```

## Конфигурация предприятий

Система автоматически загружает список активных предприятий из базы данных PostgreSQL:

```sql
SELECT number, name, host, secret, ip 
FROM enterprises 
WHERE is_enabled = true AND active = true
ORDER BY number
```

**SSH параметры (общие для всех серверов):**
```python
SSH_CONFIG = {
    "ssh_port": "5059",
    "ssh_password": "5atx9Ate@pbx"
}
```

**Для добавления нового предприятия:**
1. Добавить запись в таблицу `enterprises`
2. Установить `is_enabled = true` и `active = true`
3. Система автоматически включит его в синхронизацию

## Текущая статистика (на 03.07.2025)

### Активные предприятия
- **0100** (Radis1) - radis.vochi.lan - 10.88.10.14
- **0200** (july1) - july1.vochi.lan - 10.88.10.15  
- **0201** (Vochi) - Vochi.vochi.lan - 10.88.10.18
- **0327** (VOS) - VOS.vochi.lan - 10.88.10.25
- **0367** (june) - june.vochi.lan - 10.88.10.19

### Данные звонков
- **5,241** звонков из исторических данных (`data_source = 'downloaded'`)
- **6** звонков из live потока (`data_source = 'live'`)  
- Период: 22 июня - 3 июля 2025
- Показатели: 37.1% отвеченных, средняя длительность 93.6 сек

### Проверка статистики
```bash
# Общая статистика по источникам данных
curl -s http://localhost:8007/sync/live/status | jq '.data_sources'

# Статистика по предприятиям  
curl -s http://localhost:8007/sync/live/status | jq '.enterprise_breakdown'
```

## Логирование

Сервис ведет подробные логи:
- Автоматическая синхронизация каждые 5 минут
- Количество найденных и обработанных событий
- Ошибки подключения и обработки
- Дубликаты (игнорируются по unique_id)

## Переключение на новый сервер

1. **Текущее состояние**: События в AlternativeAPIlogs имеют статус НЕ ok
2. **После переключения**: События будут иметь статус ok  
3. **Система готова**: Всё настроено для автоматической работы

Для переключения нужно изменить настройки webhook URL на Asterisk серверах с старого на новый сервер.

## Мониторинг работы

```bash
# Проверка процесса
ps aux | grep download.py

# Проверка работоспособности
curl http://localhost:8007/health

# Статистика в реальном времени
curl http://localhost:8007/sync/live/status
```

## Добавление новых предприятий

1. **Добавить запись в таблицу `enterprises`:**
   ```sql
   INSERT INTO enterprises (number, name, host, secret, ip, is_enabled, active) 
   VALUES ('0999', 'Новое предприятие', 'new.vochi.lan', 'token_here', '10.88.10.99', true, true);
   ```

2. **Система автоматически подхватит новое предприятие** при следующей синхронизации

3. **Ручной запуск для нового предприятия:**
   ```bash
   curl -X POST http://localhost:8007/sync/live/0999
   ```

Перезапуск сервиса НЕ требуется - конфигурация читается динамически из БД при каждом запросе. 