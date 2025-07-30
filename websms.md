# WebSMS Service - Документация

## 📋 Описание

**WebSMS Service** - микросервис для отправки сервисных SMS через API провайдера [WebSMS.by](https://cabinet.websms.by). Сервис предназначен для отправки уведомлений, алертов, приветственных сообщений и других системных SMS.

### Основные возможности:
- ✅ Отправка SMS на любые номера
- ✅ Проверка баланса WebSMS аккаунта  
- ✅ Специализированные endpoints для алертов и приветственных SMS
- ✅ Полное логирование всех отправок в БД
- ✅ Health check для мониторинга
- ✅ FastAPI с автодокументацией

---

## 🚀 Быстрый старт

### Запуск сервиса
```bash
# Запуск
./sms_send.sh start

# Остановка  
./sms_send.sh stop

# Перезапуск
./sms_send.sh restart

# Статус
./sms_send.sh status

# Проверка баланса
./sms_send.sh balance
```

### Тестирование
```bash
# Проверка работы сервиса
curl http://localhost:8013/

# Отправка тестового SMS
curl -X POST http://localhost:8013/send \
  -H "Content-Type: application/json" \
  -d '{"phone": "+375296254070", "text": "Тест сообщения"}'

# Проверка баланса
curl http://localhost:8013/balance
```

---

## 📡 API Endpoints

### 1. **Статус сервиса**
```http
GET /
```

**Ответ:**
```json
{
  "service": "SMS Sending Service",
  "status": "running", 
  "timestamp": "2025-07-30T11:24:55.269590",
  "config": {
    "websms_url": "https://cabinet.websms.by/api/send/sms",
    "default_sender": "Vochi-CRM",
    "user": "info@ead.by"
  }
}
```

### 2. **Отправка SMS**
```http
POST /send
```

**Тело запроса:**
```json
{
  "phone": "+375296254070",           // Обязательно: номер в международном формате
  "text": "Текст сообщения",          // Обязательно: до 1000 символов
  "sender": "Vochi-CRM",              // Необязательно: имя отправителя (по умолчанию Vochi-CRM)
  "custom_id": "order_12345"          // Необязательно: пользовательский ID до 20 символов
}
```

**Ответ при успехе:**
```json
{
  "success": true,
  "message_id": 76443782,             // ID сообщения в WebSMS
  "price": 0.014732,                  // Стоимость за одну часть
  "parts": 1,                         // Количество частей SMS
  "amount": 0.014732,                 // Итоговая стоимость
  "custom_id": "order_12345",         // Пользовательский ID (если был указан)
  "error": null
}
```

**Ответ при ошибке:**
```json
{
  "success": false,
  "message_id": null,
  "price": null, 
  "parts": null,
  "amount": null,
  "custom_id": null,
  "error": "WebSMS API error: {'code': 4, 'description': 'Invalid user or apikey'}"
}
```

### 3. **Отправка алерта**
```http
POST /send/alert?phone=+375296254070&message=Server down
```

**Параметры:**
- `phone` - номер получателя
- `message` - текст алерта (к нему автоматически добавится "🚨 ALERT: ")
- `sender` - необязательно, имя отправителя

**Особенности:**
- Автоматически генерируется уникальный `custom_id` вида `al0730112549`
- К сообщению добавляется префикс "🚨 ALERT: "

### 4. **Приветственное SMS**
```http
POST /send/onboarding?phone=+375296254070&username=Иван
```

**Параметры:**
- `phone` - номер получателя
- `username` - имя пользователя для персонализации
- `sender` - необязательно, имя отправителя

**Особенности:**
- Отправляется стандартный текст: "Добро пожаловать, {username}! Ваш аккаунт успешно создан. Поддержка: info@ead.by"
- Автоматически генерируется `custom_id` вида `ob0730112559`

### 5. **Проверка баланса**
```http
GET /balance
```

**Ответ:**
```json
{
  "success": true,
  "timestamp": "2025-07-30T11:33:20.472857",
  "balance": {
    "status": true,
    "sms": 39.174364,                 // Баланс для SMS
    "viber": 0                        // Баланс для Viber
  }
}
```

### 6. **Health Check**
```http
GET /health
```

**Ответ:**
```json
{
  "status": "healthy",
  "timestamp": "2025-07-30T11:26:06.420513",
  "websms_api": "ok"                  // Статус доступности WebSMS API
}
```

---

## 🔧 Конфигурация

### Файлы конфигурации:
- **`send_service_sms.py`** - основной код сервиса
- **`sms_send.sh`** - скрипт управления сервисом  
- **`send_service_sms.log`** - лог файл сервиса
- **`send_service_sms.pid`** - PID файл работающего процесса

### Внутренние настройки (WEBSMS_CONFIG):
```python
WEBSMS_CONFIG = {
    "url": "https://cabinet.websms.by/api/send/sms",
    "user": "info@ead.by",
    "apikey": "bOeR6LslKf", 
    "default_sender": "Vochi-CRM",
    "timeout": 30
}
```

### Порт: **8013**

---

## 🌐 Настройка Nginx

Для внешнего доступа к SMS сервису необходимо добавить проксирование в конфигурацию Nginx:

```nginx
# В файл /etc/nginx/sites-available/default или в соответствующий конфиг

# SMS Service
location /api/sms/ {
    proxy_pass http://127.0.0.1:8013/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    
    # Увеличиваем таймауты для SMS отправки
    proxy_connect_timeout 30s;
    proxy_send_timeout 30s;
    proxy_read_timeout 30s;
}
```

**После изменения конфигурации:**
```bash
sudo nginx -t           # Проверка конфигурации
sudo systemctl reload nginx   # Перезагрузка Nginx
```

**Внешние запросы после настройки Nginx:**
```bash
# Вместо http://localhost:8013/send
curl -X POST https://yourdomain.com/api/sms/send \
  -H "Content-Type: application/json" \
  -d '{"phone": "+375296254070", "text": "External API test"}'

# Проверка баланса
curl https://yourdomain.com/api/sms/balance

# Отправка алерта
curl -X POST "https://yourdomain.com/api/sms/send/alert?phone=+375296254070&message=Critical error"
```

---

## 🗃️ База данных

### Таблица: `service_sms_send`

Все отправленные SMS логируются в PostgreSQL таблицу `service_sms_send`.

#### Структура таблицы:

```sql
CREATE TABLE service_sms_send (
    id                  BIGSERIAL PRIMARY KEY,
    
    -- Основные данные SMS
    phone               VARCHAR(20) NOT NULL,          -- Номер получателя
    text                TEXT NOT NULL,                 -- Текст сообщения  
    sender              VARCHAR(11) DEFAULT NULL,      -- Имя отправителя
    
    -- WebSMS API данные
    message_id          BIGINT DEFAULT NULL,           -- ID от WebSMS API
    custom_id           VARCHAR(20) DEFAULT NULL,      -- Пользовательский ID
    
    -- Результат отправки
    status              VARCHAR(10) NOT NULL,          -- 'success' или 'failed'
    price               DECIMAL(10,6) DEFAULT NULL,    -- Стоимость за SMS
    parts               SMALLINT DEFAULT NULL,         -- Количество частей
    amount              DECIMAL(10,6) DEFAULT NULL,    -- Итоговая сумма
    error_message       TEXT DEFAULT NULL,             -- Ошибка (если есть)
    
    -- Метаданные запроса
    service_name        VARCHAR(50) DEFAULT NULL,      -- Какой сервис отправил
    request_ip          INET DEFAULT NULL,             -- IP адрес запроса
    user_agent          VARCHAR(255) DEFAULT NULL,     -- User-Agent
    
    -- Технические данные
    response_data       JSONB DEFAULT NULL,            -- Полный ответ WebSMS API
    
    -- Временные метки
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- Время создания записи
    sent_at             TIMESTAMP DEFAULT NULL                -- Время отправки
);
```

#### Индексы:
- `idx_sms_phone` - для поиска по номеру телефона
- `idx_sms_created_at` - для сортировки по времени
- `idx_sms_message_id` - для поиска по ID WebSMS
- `idx_sms_custom_id` - для поиска по пользовательскому ID
- `idx_sms_status` - для фильтрации по статусу
- `idx_sms_service_name` - для группировки по сервисам
- `idx_sms_sent_at` - для сортировки по времени отправки

#### Полезные запросы:

**Статистика отправок за день:**
```sql
SELECT 
    status,
    COUNT(*) as count,
    SUM(amount) as total_cost
FROM service_sms_send 
WHERE created_at >= CURRENT_DATE 
GROUP BY status;
```

**Последние 10 отправленных SMS:**
```sql
SELECT phone, text, status, amount, created_at 
FROM service_sms_send 
ORDER BY created_at DESC 
LIMIT 10;
```

**SMS по конкретному номеру:**
```sql
SELECT * FROM service_sms_send 
WHERE phone = '+375296254070' 
ORDER BY created_at DESC;
```

**Статистика по сервисам:**
```sql
SELECT 
    service_name,
    COUNT(*) as total_sms,
    SUM(amount) as total_cost,
    AVG(parts) as avg_parts
FROM service_sms_send 
WHERE status = 'success'
GROUP BY service_name;
```

**Неудачные отправки с ошибками:**
```sql
SELECT phone, text, error_message, created_at 
FROM service_sms_send 
WHERE status = 'failed' 
ORDER BY created_at DESC;
```

---

## 📊 Мониторинг и логирование

### Лог файлы:
- **`send_service_sms.log`** - основной лог сервиса
- **`sms_service.log`** - дублирующий лог от приложения

### Уровни логирования:
- `INFO` - успешные отправки, запуск/остановка сервиса
- `ERROR` - ошибки API, проблемы соединения
- `DEBUG` - детальная отладочная информация

### Проверка статуса всех сервисов:
```bash
./all.sh status    # Покажет статус SMS сервиса среди прочих
```

### Мониторинг через Health Check:
```bash
# Простая проверка
curl -f http://localhost:8013/health || echo "SMS service down!"

# Проверка с деталями
curl -s http://localhost:8013/health | jq .
```

---

## 🔐 Безопасность

### Рекомендации:
1. **Не храните API ключи в коде** - используйте переменные окружения
2. **Ограничьте доступ к порту 8013** - только с localhost или доверенных IP
3. **Настройте rate limiting в Nginx** для внешних запросов
4. **Мониторьте логи** на подозрительную активность
5. **Регулярно ротируйте API ключи WebSMS**

### Nginx rate limiting:
```nginx
# Добавить в nginx.conf
http {
    limit_req_zone $binary_remote_addr zone=sms_api:10m rate=5r/m;
    
    # В location /api/sms/
    limit_req zone=sms_api burst=3 nodelay;
}
```

---

## 🛠️ Интеграция с другими сервисами

### Пример использования из Python:
```python
import requests

def send_alert_sms(phone: str, message: str):
    """Отправка SMS алерта через локальный сервис"""
    try:
        response = requests.post(
            'http://localhost:8013/send/alert',
            params={'phone': phone, 'message': message},
            timeout=30
        )
        result = response.json()
        
        if result['success']:
            print(f"SMS отправлено! ID: {result['message_id']}")
            return result['message_id']
        else:
            print(f"Ошибка отправки SMS: {result['error']}")
            return None
            
    except Exception as e:
        print(f"Ошибка соединения с SMS сервисом: {e}")
        return None

# Использование
send_alert_sms("+375296254070", "Сервер недоступен")
```

### Интеграция с curl из bash:
```bash
#!/bin/bash

send_sms() {
    local phone="$1"
    local text="$2"
    
    curl -s -X POST "http://localhost:8013/send" \
      -H "Content-Type: application/json" \
      -d "{\"phone\": \"$phone\", \"text\": \"$text\"}" \
      | jq -r '.success'
}

# Использование
if [[ $(send_sms "+375296254070" "Backup completed") == "true" ]]; then
    echo "SMS отправлено успешно"
else  
    echo "Ошибка отправки SMS"
fi
```

---

## 🔄 Управление в рамках системы

SMS сервис интегрирован в общую систему управления сервисами:

```bash
# Перезапуск всех сервисов (включая SMS)
./all.sh restart

# Статус всех сервисов  
./all.sh status

# Остановка всех сервисов
./all.sh stop
```

**Порты всех сервисов:**
- `111 (main)`: 8000
- `sms (receiving)`: 8002  
- `sms_send (sending)`: **8013** ← Наш сервис
- `admin`: 8004
- `dial`: 8005
- `plan`: 8006
- `download`: 8007
- `reboot`: 8009
- `ewelink`: 8010
- `call`: 8012

---

## 🐛 Устранение неполадок

### Сервис не запускается:
```bash
# Проверить порт
netstat -tlnp | grep :8013

# Проверить логи
tail -f send_service_sms.log

# Проверить зависимости Python
python3 -c "import fastapi, uvicorn, requests, pydantic"
```

### Ошибки отправки SMS:
```bash
# Проверить баланс
./sms_send.sh balance

# Проверить подключение к WebSMS API  
curl -s "https://cabinet.websms.by/api/balances?user=info@ead.by&apikey=bOeR6LslKf"

# Проверить валидность номера
echo "+375296254070" | grep -E '^\+375[0-9]{9}$'
```

### Проблемы с БД:
```bash
# Проверить подключение к PostgreSQL
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c '\dt service_sms_send'

# Проверить последние записи
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c 'SELECT * FROM service_sms_send ORDER BY created_at DESC LIMIT 5;'
```

---

## 📈 Производительность

### Рекомендуемые настройки:
- **Concurrent requests**: 50-100 одновременных запросов  
- **Timeout**: 30 секунд для каждого SMS
- **Rate limit**: 5 SMS в минуту (ограничение WebSMS API)
- **Memory usage**: ~50-100 MB RAM

### Масштабирование:
Для высокой нагрузки можно запустить несколько экземпляров сервиса на разных портах с load balancer.

---

## 📚 Дополнительные ресурсы

- [WebSMS.by API документация](https://cabinet.websms.by/public/client/apidoc/)
- [FastAPI документация](https://fastapi.tiangolo.com/)
- [PostgreSQL документация](https://www.postgresql.org/docs/)

---

**🎯 Сервис готов к продакшену!** 

Для получения автодокументации API перейдите по адресу: `http://localhost:8013/docs` (Swagger UI) 