# PgBouncer Migration - 09.11.2025

## Что было сделано:

### 1. Установлен PgBouncer
- Версия: 1.16.1
- Порт: **6432** (вместо прямого подключения к PostgreSQL на порту 5432)
- Конфигурация: `/etc/pgbouncer/pgbouncer.ini`

### 2. Настройки PgBouncer:
- `max_client_conn`: 500 (максимум подключений от клиентов)
- `default_pool_size`: 25 (размер пула для каждой БД)
- `max_db_connections`: 50 (максимум реальных подключений к PostgreSQL)
- `pool_mode`: session (режим сессий)

### 3. Изменены все приложения:
Порт подключения к PostgreSQL изменён с **5432** на **6432** в следующих файлах:

**Основные сервисы:**
- `app/services/postgres.py` - главный модуль подключения
- `main.py` - webhook сервис
- `logger.py` - логгер звонков
- `telegram_auth_handler_v2.py` - обработчики авторизации ботов
- `telegram_auth_service.py` - сервис авторизации

**Интеграции:**
- `uon.py` - интеграция U-ON
- `integration_cache.py` - кэш интеграций
- `integration_client.py` - клиент интеграций
- `ms.py` - МойСклад
- `24.py` - Bitrix24
- `retailcrm.py` - RetailCRM

**Другие сервисы:**
- `enterprise_admin_service.py` - админка предприятия
- `dial_service.py` - сервис набора
- `desk.py` - desk сервис
- `download.py` - загрузка записей
- `smart.py` - умный редирект
- `auth.py` - авторизация
- `send_user_sms.py` - отправка SMS пользователям
- `send_service_sms.py` - отправка сервисных SMS
- `mini_app/miniapp_service.py` - мини-приложение
- `app/routers/admin.py` - роутеры админки
- `app/database.py` - модуль БД

**Утилиты:**
- `clear_enterprises.py`
- `clear_enterprises_async.py`
- `daily_recordings_sync.py`
- `check_db_structure.py`
- `call_tester.py`
- `debug_call_tester.py`

### 4. Результат:

**БЫЛО:**
- 78 ботов + 23 сервиса = ~100 прямых подключений к PostgreSQL
- Пиковая нагрузка при перезапуске: 95-100 сессий
- Время готовности после перезапуска: ~5 минут

**СТАЛО:**
- 78 ботов + 23 сервиса → PgBouncer → 25-50 подключений к PostgreSQL
- Ожидаемая пиковая нагрузка: 40-50 сессий
- Ожидаемое время готовности: <1 минуты

## Как проверить:

### Статус PgBouncer:
```bash
systemctl status pgbouncer
```

### Подключение к админке PgBouncer:
```bash
psql -h 127.0.0.1 -p 6432 -U postgres pgbouncer
```

Команды в админке:
- `SHOW POOLS;` - показать пулы подключений
- `SHOW CLIENTS;` - показать клиентские подключения
- `SHOW SERVERS;` - показать серверные подключения к PostgreSQL
- `SHOW STATS;` - показать статистику

### Проверка подключений к PostgreSQL:
```bash
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "SELECT count(*) as connections, state FROM pg_stat_activity WHERE datname='postgres' GROUP BY state;"
```

## Откат (если нужно):

Если что-то пойдёт не так, можно откатить изменения:

```bash
# Остановить PgBouncer
systemctl stop pgbouncer

# Вернуть порт 5432 во всех файлах
cd /root/asterisk-webhook
find . -name "*.py" -type f -exec sed -i 's/port=6432/port=5432/g' {} \;
find . -name "*.py" -type f -exec sed -i "s/'port': 6432/'port': 5432/g" {} \;
find . -name "*.py" -type f -exec sed -i 's/"port": 6432/"port": 5432/g' {} \;

# Перезапустить все сервисы
./all.sh restart
```

## Дата миграции: 09.11.2025

