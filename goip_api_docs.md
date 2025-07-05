# GoIP Management Service API

## Обзор

GoIP Management Service - это микросервис для управления GoIP устройствами через mftp.vochi.by.

- **Порт**: 8008
- **Базовый URL**: http://127.0.0.1:8008
- **Автоматическое сканирование**: каждые 5 минут

## Управление сервисом

```bash
# Запуск сервиса
./goip.sh start

# Остановка сервиса
./goip.sh stop

# Перезапуск сервиса
./goip.sh restart

# Проверка статуса
./goip.sh status
```

## API Endpoints

### 1. Главная страница
**GET** `/`

Возвращает информацию о сервисе.

```json
{
    "message": "GoIP Management Service",
    "version": "1.0.0"
}
```

### 2. Проверка здоровья
**GET** `/health`

Проверяет подключение к базе данных.

```json
{
    "status": "healthy",
    "timestamp": "2025-07-05T09:26:18.983516"
}
```

### 3. Список всех устройств
**GET** `/devices`

Возвращает список всех GoIP устройств.

```json
[
    {
        "id": 187,
        "gateway_name": "Vochi-Main",
        "enterprise_number": "0367",
        "port": 38000,
        "port_scan_status": "active",
        "device_model": "GoIP16",
        "serial_last4": "0960",
        "line_count": 16,
        "device_password": "d68409d67e3b4b87a6675e76dae74a85"
    }
]
```

### 4. Информация о конкретном устройстве
**GET** `/devices/{gateway_name}`

Возвращает информацию о конкретном устройстве.

**Параметры:**
- `gateway_name` - имя шлюза (например, "Vochi-Main")

### 5. Статус линий устройства
**GET** `/devices/{gateway_name}/lines`

Возвращает статус всех линий устройства.

```json
[
    {
        "line": 1,
        "auth_id": "0001363",
        "rssi": "&nbsp;31",
        "busy_status": "&nbsp;OFF"
    },
    {
        "line": 2,
        "auth_id": "0001364",
        "rssi": "&nbsp;27",
        "busy_status": "&nbsp;OFF"
    }
]
```

### 6. Перезагрузка устройства
**POST** `/devices/{gateway_name}/reboot`

Перезагружает GoIP устройство.

**Ответ при успехе:**
```json
{
    "message": "Device Vochi-Main rebooted successfully"
}
```

### 7. Ручное сканирование портов
**POST** `/scan`

Запускает ручное сканирование всех устройств.

```json
{
    "results": [
        {
            "device": "Vochi-Main",
            "port": 38000,
            "status": "active",
            "model": "GoIP16"
        },
        {
            "device": "Biz",
            "status": "inactive"
        }
    ]
}
```

## Статусы устройств

- **active** - устройство найдено и доступно
- **inactive** - устройство не найдено на mftp
- **error** - ошибка при получении информации об устройстве
- **unknown** - статус неизвестен (по умолчанию)

## Примеры использования

### Получение списка активных устройств
```bash
curl -s http://127.0.0.1:8008/devices | jq '.[] | select(.port_scan_status == "active")'
```

### Перезагрузка устройства
```bash
curl -X POST http://127.0.0.1:8008/devices/Vochi-Main/reboot
```

### Получение статуса линий
```bash
curl -s http://127.0.0.1:8008/devices/Vochi-Main/lines | jq '.[] | {line: .line, auth_id: .auth_id, rssi: .rssi}'
```

### Запуск ручного сканирования
```bash
curl -X POST http://127.0.0.1:8008/scan
```

## Логи

Логи сервиса записываются в файл `/var/log/goip_service.log`.

```bash
# Просмотр логов
tail -f /var/log/goip_service.log

# Последние 50 строк
tail -50 /var/log/goip_service.log
```

## Конфигурация

Сервис использует жестко заданные настройки в коде `goip_service.py`:

```python
# PostgreSQL подключение
POSTGRES_HOST = 'localhost'
POSTGRES_PORT = 5432
POSTGRES_DB = 'postgres'
POSTGRES_USER = 'postgres'
POSTGRES_PASSWORD = 'r/Yskqh/ZbZuvjb2b3ahfg=='

# GoIP микросервис настройки
GOIP_SERVICE_PORT = 8008
GOIP_SCAN_INTERVAL = 300  # 5 минут
GOIP_SCAN_TIMEOUT = 30

# mftp.vochi.by креды (жестко заданы в коде)
MFTP_HOST = 'mftp.vochi.by'
MFTP_PORT = 8086
MFTP_USERNAME = 'admin'
MFTP_PASSWORD = 'cdjkjxbdct4070+37529AAA'

# GoIP базовые креды
GOIP_DEFAULT_USERNAME = 'admin'
GOIP_DEFAULT_PASSWORD = 'admin'
```

**Примечание:** Креды mftp.vochi.by хранятся жестко в коде для избежания конфликтов с Pydantic Settings основного приложения при использовании переменных окружения.

## Архитектура

Сервис состоит из следующих компонентов:

1. **FastAPI приложение** - REST API для управления устройствами
2. **Фоновое сканирование** - автоматическое обновление информации о портах
3. **База данных PostgreSQL** - хранение информации об устройствах
4. **mftp интеграция** - поиск портов устройств
5. **GoIP веб-интерфейс** - получение информации и управление устройствами

## Безопасность

- Креды устройств хранятся в таблице `enterprises`
- Подключение к GoIP через Basic Auth
- Логи не содержат паролей
- Таймауты для предотвращения зависания запросов 