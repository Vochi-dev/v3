# Asterisk Call Management API

Сервис для управления звонками на удаленных хостах Asterisk через SSH CLI команды.

## 📋 Описание

**Asterisk Call Management API** - это FastAPI сервис, который позволяет инициировать звонки на удаленных Asterisk хостах через безопасные SSH соединения. Сервис предоставляет REST API для внешних систем и автоматически определяет нужный Asterisk хост на основе авторизации предприятия.

### Основные возможности:
- ✅ Инициация звонков через REST API
- ✅ Автоматическое определение Asterisk хоста по предприятию
- ✅ Авторизация через секретные ключи предприятий
- ✅ SSH подключение без изменений на удаленных хостах
- ✅ Логирование всех операций
- ✅ Интеграция с системой мониторинга

---

## 🏗️ Архитектура

```
Внешний запрос → Nginx → asterisk.py (8018) → SSH CLI → Asterisk → Звонок
```

### Компоненты:
1. **asterisk.py** - FastAPI сервис на порту 8018
2. **asterisk.sh** - скрипт управления сервисом
3. **Nginx** - роутинг `/api/` на порт 8018
4. **PostgreSQL** - проверка авторизации предприятий
5. **SSH** - безопасное подключение к Asterisk хостам

---

## 🌐 API Endpoints

### `GET /api/makecallexternal`

Инициация внешнего звонка.

**Параметры:**
- `code` (string, required) - внутренний номер (откуда звонить)
- `phone` (string, required) - номер телефона (куда звонить)
- `clientId` (string, required) - secret из таблицы enterprises

**Пример запроса:**
```
GET https://bot.vochi.by/api/makecallexternal?code=150&phone=%2B375296254070&clientId=d68409d67e3b4b87a6675e76dae74a85
```

**Успешный ответ (200):**
```json
{
    "success": true,
    "message": "Call initiated successfully: 150 -> +375296254070",
    "enterprise": "june",
    "enterprise_number": "0367",
    "from_ext": "150",
    "to_phone": "+375296254070",
    "host_ip": "10.88.10.19",
    "response_time_ms": 1399.39
}
```

**Ошибка авторизации (401):**
```json
{
    "detail": "Invalid clientId"
}
```

**Ошибка инициации звонка (500):**
```json
{
    "detail": "Call initiation failed: SSH command failed: Connection timeout"
}
```

### `GET /health`

Health check endpoint.

**Ответ:**
```json
{
    "status": "healthy",
    "service": "asterisk-call-management",
    "port": 8018
}
```

### `GET /api/status`

Статус API и подключений.

**Ответ:**
```json
{
    "service": "asterisk-call-management",
    "version": "1.0.0",
    "database": "connected",
    "asterisk_config": {
        "method": "SSH CLI",
        "ssh_port": 5059,
        "ssh_user": "root"
    }
}
```

---

## 🔧 Техническое решение

### SSH CLI подход

Сервис использует SSH подключения для выполнения Asterisk CLI команд вместо AMI интерфейса.

**Команда инициации звонка:**
```bash
asterisk -rx "channel originate LOCAL/150@inoffice application Dial LOCAL/+375296254070@inoffice"
```

**Полная SSH команда:**
```bash
sshpass -p '5atx9Ate@pbx' ssh -p 5059 -o StrictHostKeyChecking=no root@10.88.10.19 'asterisk -rx "channel originate LOCAL/150@inoffice application Dial LOCAL/+375296254070@inoffice"'
```

### Преимущества SSH подхода:
- ✅ **Нет изменений на удаленных хостах**
- ✅ **Использует существующую SSH инфраструктуру**
- ✅ **Простота настройки**
- ✅ **Безопасность через SSH**
- ✅ **Не требует настройки AMI пользователей**

---

## ⚙️ Конфигурация

### Настройки базы данных
```python
DB_CONFIG = {
    "user": POSTGRES_USER,
    "password": POSTGRES_PASSWORD,
    "database": POSTGRES_DB,
    "host": POSTGRES_HOST,
    "port": POSTGRES_PORT,
}
```

### Настройки SSH
```python
ASTERISK_CONFIG = {
    "ssh_port": 5059,
    "ssh_user": "root",
    "ssh_password": "5atx9Ate@pbx"
}
```

### Требования к системе
- Python 3.8+
- `sshpass` (для SSH авторизации по паролю)
- PostgreSQL клиент
- Доступ к сети 10.88.10.x

---

## 🚀 Управление сервисом

### Использование asterisk.sh

```bash
# Запуск сервиса
./asterisk.sh start

# Остановка сервиса
./asterisk.sh stop

# Перезапуск сервиса
./asterisk.sh restart

# Статус сервиса
./asterisk.sh status

# Просмотр логов
./asterisk.sh logs

# Тест сервиса
./asterisk.sh test
```

### Интеграция с all.sh

Сервис интегрирован в общую систему управления:

```bash
# Запуск всех сервисов (включая asterisk)
./all.sh start

# Остановка всех сервисов
./all.sh stop

# Статус всех сервисов
./all.sh status
```

---

## 📊 Мониторинг

### Логирование

Логи сохраняются в `asterisk_service.log`:

```
2025-08-03 08:00:20,000 - asterisk - INFO - 🚀 Запрос на звонок: 150 -> +375296254070, clientId: d68409d6...
2025-08-03 08:00:20,084 - asterisk - INFO - ✅ Клиент авторизован: june (0367)
2025-08-03 08:00:20,084 - asterisk - INFO - 🔗 SSH подключение к 10.88.10.19: 150 -> +375296254070
2025-08-03 08:00:21,396 - asterisk - INFO - ✅ CLI команда выполнена на 10.88.10.19: 150 -> +375296254070
```

### Админ панель

Сервис добавлен в модалку "Services" в админ панели для мониторинга:
- 🟢 Статус порта 8018
- 📊 Информация о процессе
- 🔄 Возможность перезапуска

---

## 🔒 Безопасность

### Авторизация предприятий

Каждый запрос проверяется по таблице `enterprises`:

```sql
SELECT number, name, ip 
FROM enterprises 
WHERE secret = $1 AND active = true
```

### SSH безопасность

- Подключение через стандартные SSH кредиты
- Использование `StrictHostKeyChecking=no` для автоматизации
- Таймауты подключения (10 секунд)

### Валидация параметров

- Проверка обязательных параметров
- Санитизация входных данных
- Защита от SQL-инъекций через параметризованные запросы

---

## 🧪 Тестирование

### Локальный тест

```bash
curl -s "http://localhost:8018/health"
```

### Тест через Nginx

```bash
curl -s "https://bot.vochi.by/api/makecallexternal?code=150&phone=%2B375296254070&clientId=YOUR_SECRET"
```

### Проверка логов

```bash
tail -f asterisk_service.log
```

---

## 🛠️ Troubleshooting

### Частые проблемы

**1. SSH Connection Timeout**
```
Проблема: SSH timeout to 10.88.10.19
Решение: Проверить сетевое подключение и доступность хоста
```

**2. sshpass not found**
```
Проблема: SSH client (sshpass) not available
Решение: Установить sshpass: apt-get install sshpass
```

**3. Invalid clientId**
```
Проблема: Неверный или неактивный clientId
Решение: Проверить таблицу enterprises, поле secret и active = true
```

**4. Database connection error**
```
Проблема: Нет подключения к PostgreSQL
Решение: Проверить настройки БД в app/config.py
```

### Диагностика

```bash
# Проверка статуса сервиса
./asterisk.sh status

# Проверка логов
./asterisk.sh logs

# Тест API
./asterisk.sh test

# Проверка порта
netstat -tuln | grep 8018

# Проверка процесса
ps aux | grep asterisk
```

---

## 📈 Производительность

### Типичные показатели

- **Response time**: 1000-2000ms (включая SSH подключение)
- **Timeout SSH**: 10 секунд
- **Timeout команды**: 15 секунд
- **Concurrent requests**: Поддерживает множественные запросы

### Оптимизация

- SSH подключения оптимизированы с `ConnectTimeout=10`
- Используется connection pooling для PostgreSQL
- Логирование только важных событий

---

## 🔄 Развитие

### Планы развития

1. **Connection pooling для SSH** - переиспользование SSH соединений
2. **Поддержка других Asterisk команд** - получение статуса, история звонков
3. **WebSocket уведомления** - real-time статус звонков
4. **Метрики Prometheus** - детальный мониторинг
5. **Rate limiting** - ограничение частоты запросов

### Интеграция

Сервис готов для интеграции с:
- CRM системами
- Telegram ботами
- Внешними приложениями
- Системами мониторинга

---

## 📞 Примеры использования

### Интеграция в CRM

```javascript
// JavaScript пример
async function initiateCall(extension, phone) {
    const response = await fetch(`https://bot.vochi.by/api/makecallexternal?code=${extension}&phone=${encodeURIComponent(phone)}&clientId=${CLIENT_SECRET}`);
    const result = await response.json();
    
    if (result.success) {
        console.log(`Звонок инициирован: ${result.message}`);
    } else {
        console.error(`Ошибка: ${result.detail}`);
    }
}
```

### Telegram бот интеграция

```python
# Python пример
import httpx

async def make_call_from_bot(extension: str, phone: str, client_secret: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://bot.vochi.by/api/makecallexternal",
            params={
                "code": extension,
                "phone": phone,
                "clientId": client_secret
            }
        )
        return response.json()
```

---

## 📝 История версий

### v1.0.0 (2025-08-03)
- ✅ Первый релиз
- ✅ SSH CLI реализация
- ✅ REST API с авторизацией
- ✅ Интеграция с админ панелью
- ✅ Логирование и мониторинг

---

**Сервис готов к production использованию! 🚀**