# User WebSMS Service - Документация

## 📋 Описание

**User WebSMS Service** - микросервис для отправки SMS от имени предприятий через API провайдера [WebSMS.by](https://cabinet.websms.by). Каждое предприятие использует свои собственные WebSMS credentials, хранящиеся в поле `custom_domain` таблицы `enterprises`.

### Основные возможности:
- ✅ Отправка SMS от имени конкретного предприятия
- ✅ Индивидуальные WebSMS credentials для каждого предприятия
- ✅ Полное логирование всех отправок в БД с информацией о предприятии
- ✅ Health check для мониторинга
- ✅ FastAPI с автодокументацией
- ✅ Валидация номеров предприятий

---

## 🚀 Быстрый старт

### Запуск сервиса
```bash
# Запуск
./send_user_sms.sh start

# Остановка  
./send_user_sms.sh stop

# Перезапуск
./send_user_sms.sh restart

# Статус
./send_user_sms.sh status

# Проверка баланса предприятия
./send_user_sms.sh balance 0367
```

### Тестирование
```bash
# Проверка работы сервиса
curl http://localhost:8014/

# Отправка тестового SMS от предприятия 0367
curl -X POST http://localhost:8014/send \
  -H "Content-Type: application/json" \
  -d '{
    "enterprise_number": "0367",
    "phone": "+375296254070", 
    "text": "Тест сообщения от предприятия"
  }'

# Проверка health
curl http://localhost:8014/health
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
  "service": "User SMS Sending Service",
  "status": "running", 
  "timestamp": "2025-07-30T12:15:00.123456",
  "config": {
    "websms_url": "https://cabinet.websms.by/api/send/sms",
    "default_sender": "Vochi-CRM",
    "port": 8014
  }
}
```

### 2. **Отправка SMS от имени предприятия**
```http
POST /send
```

**Тело запроса:**
```json
{
  "enterprise_number": "0367",           // Обязательно: номер предприятия из таблицы enterprises
  "phone": "+375296254070",              // Обязательно: номер в международном формате
  "text": "Сообщение от предприятия",    // Обязательно: до 1000 символов
  "sender": "MyCompany",                 // Необязательно: имя отправителя (по умолчанию Vochi-CRM)
  "custom_id": "order_98765"             // Необязательно: пользовательский ID до 20 символов
}
```

**Ответ при успехе:**
```json
{
  "success": true,
  "message_id": 76445500,               // ID сообщения в WebSMS
  "price": 0.014732,                    // Стоимость за одну часть
  "parts": 1,                           // Количество частей SMS
  "amount": 0.014732,                   // Итоговая стоимость
  "custom_id": "order_98765",           // Пользовательский ID (если был указан)
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
  "error": "Enterprise 0999 not found or has no WebSMS configuration"
}
```

**HTTP коды ошибок:**
- `400` - Неверный формат номера телефона
- `404` - Предприятие не найдено или не имеет WebSMS настроек
- `500` - Внутренняя ошибка сервера

### 3. **Health Check**
```http
GET /health
```

**Ответ:**
```json
{
  "status": "healthy",
  "timestamp": "2025-07-30T12:16:00.987654",
  "database": "ok",                     // Статус подключения к БД
  "port": 8014
}
```

### 4. **Проверка баланса предприятия**

Проверка баланса WebSMS для конкретного предприятия через командную строку:

```bash
./send_user_sms.sh balance <enterprise_number>
```

**Пример использования:**
```bash
# Проверка баланса предприятия 0367
./send_user_sms.sh balance 0367
```

**Результат при успехе:**
```
🔍 Проверка баланса WebSMS для предприятия 0367...
💰 БАЛАНС WEBSMS:
============================================================
🏢 Предприятие: 0367 (june)
👤 Пользователь: info@ead.by
------------------------------------------------------------
   📱 SMS: 39.056508 BYN
   💬 Viber: 0 BYN
   💰 Общий доступный баланс: 39.056508 BYN
============================================================
```

**Результат при ошибке:**
```
❌ Предприятие 9999 не найдено или не имеет WebSMS настроек
   Проверьте поле custom_domain в таблице enterprises
```

**Особенности:**
- Использует credentials из поля `custom_domain` таблицы `enterprises`
- Отображает детальную информацию о предприятии
- Показывает отдельно баланс SMS и Viber
- Автоматически форматирует вывод для удобного чтения

---

## 🔧 Конфигурация

### Файлы конфигурации:
- **`send_user_sms.py`** - основной код сервиса
- **`send_user_sms.sh`** - скрипт управления сервисом  
- **`send_user_sms.log`** - лог файл сервиса
- **`send_user_sms.pid`** - PID файл работающего процесса

### Внутренние настройки (WEBSMS_CONFIG):
```python
WEBSMS_CONFIG = {
    "url": "https://cabinet.websms.by/api/send/sms",
    "balance_url": "https://cabinet.websms.by/api/balances",
    "default_sender": "Vochi-CRM",
    "timeout": 30
}
```

### Порт: **8014**

---

## 🏢 Настройка предприятий

### Формат хранения credentials в БД:

В таблице `enterprises` поле `custom_domain` должно содержать:
```
user@domain.com API_KEY
```

**Пример для предприятия 0367:**
```sql
UPDATE enterprises 
SET custom_domain = 'info@company.by bOeR6LslKf'
WHERE number = '0367';
```

### Проверка настроек предприятия:
```sql
SELECT number, name, custom_domain 
FROM enterprises 
WHERE number = '0367';
```

**Результат:**
```
 number | name |     custom_domain      
--------+------+------------------------
 0367   | june | info@ead.by bOeR6LslKf
```

### Требования к custom_domain:
- ✅ Не должно быть NULL или пустым
- ✅ Должно содержать пробел как разделитель
- ✅ До пробела - email пользователя WebSMS
- ✅ После пробела - API ключ WebSMS

---

## 🌐 Настройка Nginx

Для внешнего доступа к User SMS сервису добавьте в конфигурацию Nginx:

```nginx
# В файл /etc/nginx/sites-available/default или в соответствующий конфиг

# User SMS Service  
location /api/user-sms/ {
    proxy_pass http://127.0.0.1:8014/;
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
sudo nginx -t                    # Проверка конфигурации
sudo systemctl reload nginx     # Перезагрузка Nginx
```

**Внешние запросы после настройки Nginx:**
```bash
# Отправка SMS через внешний API
curl -X POST https://yourdomain.com/api/user-sms/send \
  -H "Content-Type: application/json" \
  -d '{
    "enterprise_number": "0367",
    "phone": "+375296254070", 
    "text": "External User SMS test"
  }'

# Проверка health
curl https://yourdomain.com/api/user-sms/health
```

---

## 🗃️ База данных

### Таблица: `user_sms_send`

Все SMS, отправленные от имени предприятий, логируются в PostgreSQL таблицу `user_sms_send`.

#### Структура таблицы:

```sql
CREATE TABLE user_sms_send (
    id                  BIGSERIAL PRIMARY KEY,
    
    -- Информация о предприятии
    enterprise_number   VARCHAR(10) NOT NULL,          -- Номер предприятия
    enterprise_name     VARCHAR(255) DEFAULT NULL,     -- Название предприятия
    
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
- `idx_user_sms_enterprise_number` - для поиска по номеру предприятия
- `idx_user_sms_phone` - для поиска по номеру телефона
- `idx_user_sms_created_at` - для сортировки по времени
- `idx_user_sms_message_id` - для поиска по ID WebSMS
- `idx_user_sms_custom_id` - для поиска по пользовательскому ID
- `idx_user_sms_status` - для фильтрации по статусу
- `idx_user_sms_service_name` - для группировки по сервисам
- `idx_user_sms_sent_at` - для сортировки по времени отправки

#### Полезные запросы:

**Статистика по предприятиям за день:**
```sql
SELECT 
    enterprise_number,
    enterprise_name,
    COUNT(*) as sms_count,
    SUM(amount) as total_cost,
    COUNT(CASE WHEN status = 'success' THEN 1 END) as successful,
    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed
FROM user_sms_send 
WHERE created_at >= CURRENT_DATE 
GROUP BY enterprise_number, enterprise_name
ORDER BY sms_count DESC;
```

**SMS конкретного предприятия:**
```sql
SELECT 
    phone, 
    LEFT(text, 50) as message_preview,
    status, 
    amount, 
    created_at 
FROM user_sms_send 
WHERE enterprise_number = '0367' 
ORDER BY created_at DESC 
LIMIT 10;
```

**Последние отправки всех предприятий:**
```sql
SELECT 
    enterprise_number || ' (' || enterprise_name || ')' as enterprise,
    phone, 
    LEFT(text, 30) as message,
    status, 
    amount,
    TO_CHAR(created_at, 'DD.MM HH24:MI') as sent_time
FROM user_sms_send 
ORDER BY created_at DESC 
LIMIT 15;
```

**Неудачные отправки по предприятиям:**
```sql
SELECT 
    enterprise_number,
    enterprise_name,
    phone, 
    LEFT(text, 40) as message,
    error_message,
    created_at 
FROM user_sms_send 
WHERE status = 'failed' 
ORDER BY created_at DESC;
```

**Статистика расходов по предприятиям:**
```sql
SELECT 
    enterprise_number,
    enterprise_name,
    COUNT(*) as total_sms,
    SUM(amount) as total_spent,
    AVG(amount) as avg_cost_per_sms,
    SUM(parts) as total_parts
FROM user_sms_send 
WHERE status = 'success' 
  AND created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY enterprise_number, enterprise_name 
ORDER BY total_spent DESC;
```

---

## 📊 Мониторинг и логирование

### Лог файлы:
- **`send_user_sms.log`** - основной лог сервиса
- **`user_sms_service.log`** - дублирующий лог от приложения

### Уровни логирования:
- `INFO` - успешные отправки, получение credentials предприятий
- `WARNING` - предприятие не найдено или не имеет custom_domain
- `ERROR` - ошибки API, проблемы БД, неверный формат custom_domain
- `DEBUG` - детальная отладочная информация

### Проверка статуса всех сервисов:
```bash
./all.sh status    # Покажет статус User SMS сервиса среди прочих
```

### Мониторинг через Health Check:
```bash
# Простая проверка
curl -f http://localhost:8014/health || echo "User SMS service down!"

# Проверка с деталями
curl -s http://localhost:8014/health | jq .
```

### Проверка баланса предприятий:
```bash
# Проверка баланса конкретного предприятия
./send_user_sms.sh balance 0367

# Проверка всех предприятий с WebSMS настройками
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c \
"SELECT number, name, custom_domain FROM enterprises WHERE custom_domain IS NOT NULL AND custom_domain != '';"

# Автоматическая проверка баланса для активных предприятий
for enterprise in $(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c \
"SELECT number FROM enterprises WHERE custom_domain IS NOT NULL AND custom_domain != '' AND active = true;"); do
    echo "=== Предприятие $enterprise ==="
    ./send_user_sms.sh balance $enterprise
    echo ""
done
```

---

## 🔐 Безопасность

### Рекомендации:
1. **Защитите credentials предприятий** - ограничьте доступ к таблице `enterprises`
2. **Валидируйте номера предприятий** - проверяйте права доступа перед отправкой
3. **Ограничьте доступ к порту 8014** - только с localhost или доверенных IP
4. **Настройте rate limiting в Nginx** для внешних запросов
5. **Мониторьте подозрительную активность** - отслеживайте неавторизованные запросы
6. **Регулярно ротируйте API ключи** предприятий в WebSMS

### Nginx rate limiting для User SMS:
```nginx
# Добавить в nginx.conf
http {
    limit_req_zone $binary_remote_addr zone=user_sms_api:10m rate=3r/m;
    
    # В location /api/user-sms/
    limit_req zone=user_sms_api burst=2 nodelay;
}
```

### Аудит безопасности:
```sql
-- Проверка попыток отправки от несуществующих предприятий
SELECT DISTINCT service_name, request_ip, user_agent, COUNT(*)
FROM user_sms_send 
WHERE status = 'failed' 
  AND error_message LIKE '%not found%'
  AND created_at >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY service_name, request_ip, user_agent
ORDER BY count DESC;
```

---

## 🛠️ Интеграция с другими сервисами

### Пример использования из Python:
```python
import requests
import subprocess

def send_enterprise_sms(enterprise_number: str, phone: str, message: str):
    """Отправка SMS от имени предприятия через User SMS сервис"""
    try:
        response = requests.post(
            'http://localhost:8014/send',
            json={
                'enterprise_number': enterprise_number,
                'phone': phone,
                'text': message
            },
            timeout=30
        )
        
        result = response.json()
        
        if response.status_code == 200 and result['success']:
            print(f"SMS отправлено! ID: {result['message_id']}, Цена: {result['amount']} BYN")
            return result['message_id']
        else:
            print(f"Ошибка отправки SMS: {result.get('error', 'Unknown error')}")
            return None
            
    except Exception as e:
        print(f"Ошибка соединения с User SMS сервисом: {e}")
        return None

# Использование
message_id = send_enterprise_sms("0367", "+375296254070", "Ваш заказ готов")
if message_id:
    print(f"SMS успешно отправлен с ID: {message_id}")

def check_enterprise_balance(enterprise_number: str):
    """Проверка баланса предприятия из Python кода"""
    try:
        # Запускаем команду проверки баланса
        result = subprocess.run(
            ['./send_user_sms.sh', 'balance', enterprise_number],
            capture_output=True, text=True, cwd='/root/asterisk-webhook'
        )
        
        if result.returncode == 0:
            print(f"Баланс предприятия {enterprise_number}:")
            print(result.stdout)
            return True
        else:
            print(f"Ошибка проверки баланса: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"Ошибка выполнения команды: {e}")
        return False

# Использование
if check_enterprise_balance("0367"):
    # Отправляем SMS только если баланс доступен
    send_enterprise_sms("0367", "+375296254070", "Уведомление")
```

### Интеграция с curl из bash:
```bash
#!/bin/bash

send_enterprise_sms() {
    local enterprise="$1"
    local phone="$2"
    local text="$3"
    
    local response=$(curl -s -X POST "http://localhost:8014/send" \
        -H "Content-Type: application/json" \
        -d "{
            \"enterprise_number\": \"$enterprise\",
            \"phone\": \"$phone\", 
            \"text\": \"$text\"
        }")
    
    local success=$(echo "$response" | jq -r '.success')
    
    if [[ "$success" == "true" ]]; then
        local message_id=$(echo "$response" | jq -r '.message_id')
        echo "SMS отправлено успешно! ID: $message_id"
        return 0
    else
        local error=$(echo "$response" | jq -r '.error')
        echo "Ошибка отправки SMS: $error"
        return 1
    fi
}

# Использование
if send_enterprise_sms "0367" "+375296254070" "Уведомление о заказе"; then
    echo "Уведомление отправлено клиенту"
else  
    echo "Не удалось отправить уведомление"
fi
```

---

## 🔄 Управление в рамках системы

User SMS сервис интегрирован в общую систему управления сервисами:

```bash
# Перезапуск всех сервисов (включая User SMS)
./all.sh restart

# Статус всех сервисов  
./all.sh status

# Остановка всех сервисов
./all.sh stop
```

**Порты всех сервисов:**
- `111 (main)`: 8000
- `sms (receiving)`: 8002  
- `sms_send (service)`: 8013
- `send_user_sms (enterprise)`: **8014** ← Наш сервис
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
netstat -tlnp | grep :8014

# Проверить логи
tail -f send_user_sms.log

# Проверить зависимости Python
python3 -c "import fastapi, uvicorn, requests, pydantic, psycopg2"
```

### Ошибки отправки SMS:
```bash
# Проверить настройки предприятия в БД
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c \
"SELECT number, name, custom_domain FROM enterprises WHERE number = '0367';"

# Проверить формат custom_domain (должен содержать пробел)
echo "info@ead.by bOeR6LslKf" | grep -E '^[^ ]+ [^ ]+$'

# Проверить баланс предприятия
./send_user_sms.sh balance 0367

# Проверить подключение к WebSMS API напрямую
curl -s "https://cabinet.websms.by/api/balances?user=info@ead.by&apikey=bOeR6LslKf"
```

### Проблемы с балансом:
```bash
# Проверить баланс конкретного предприятия
./send_user_sms.sh balance 0367

# Если баланс недоступен, проверить credentials
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c \
"SELECT number, name, 
 SPLIT_PART(custom_domain, ' ', 1) as websms_user,
 SPLIT_PART(custom_domain, ' ', 2) as websms_apikey
 FROM enterprises WHERE number = '0367';"

# Проверить работают ли credentials
user=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c \
"SELECT SPLIT_PART(custom_domain, ' ', 1) FROM enterprises WHERE number = '0367';")
apikey=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c \
"SELECT SPLIT_PART(custom_domain, ' ', 2) FROM enterprises WHERE number = '0367';")

curl -s "https://cabinet.websms.by/api/balances?user=$user&apikey=$apikey"
```

### Проблемы с БД:
```bash
# Проверить таблицу user_sms_send
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c '\dt user_sms_send'

# Проверить последние записи
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c \
'SELECT enterprise_number, phone, status, created_at FROM user_sms_send ORDER BY created_at DESC LIMIT 5;'
```

### Проблемы с предприятиями:
```bash
# Найти предприятия без WebSMS настроек
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c \
"SELECT number, name FROM enterprises WHERE custom_domain IS NULL OR custom_domain = '';"

# Проверить формат всех custom_domain
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c \
"SELECT number, name, custom_domain FROM enterprises WHERE custom_domain IS NOT NULL AND custom_domain != '';"
```

---

## 📈 Производительность

### Рекомендуемые настройки:
- **Concurrent requests**: 30-50 одновременных запросов  
- **Timeout**: 30 секунд для каждого SMS
- **Rate limit**: 3 SMS в минуту на предприятие (ограничение WebSMS API)
- **Memory usage**: ~30-60 MB RAM

### Оптимизация для предприятий:
```sql
-- Индекс для быстрого поиска предприятий с WebSMS
CREATE INDEX idx_enterprises_custom_domain 
ON enterprises (custom_domain) 
WHERE custom_domain IS NOT NULL AND custom_domain != '';

-- Партиционирование user_sms_send по месяцам (для больших объемов)
-- CREATE TABLE user_sms_send_2025_01 PARTITION OF user_sms_send 
-- FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
```

---

## 📚 Дополнительные ресурсы

- [WebSMS.by API документация](https://cabinet.websms.by/public/client/apidoc/)
- [FastAPI документация](https://fastapi.tiangolo.com/)
- [PostgreSQL документация](https://www.postgresql.org/docs/)
- [Основной SMS сервис документация](websms.md)

---

## 🔄 Интеграция с основным SMS сервисом

### Различия сервисов:

| Характеристика | Service SMS (8013) | User SMS (8014) |
|---|---|---|
| **Назначение** | Системные SMS | SMS от предприятий |
| **Credentials** | Хардкод в коде | Из БД предприятий |
| **Таблица логов** | `service_sms_send` | `user_sms_send` |
| **Доп. поля** | service_name | enterprise_number, enterprise_name |
| **Типы SMS** | alert, onboarding, direct | только direct |
| **Использование** | Внутренние уведомления | Клиентские сообщения |

### Выбор сервиса:
- **Используйте 8013** для системных уведомлений (алерты, регистрация и т.д.)
- **Используйте 8014** для SMS от имени конкретных предприятий

---

**🎯 User SMS сервис готов к продакшену!** 

Для получения автодокументации API перейдите по адресу: `http://localhost:8014/docs` (Swagger UI) 