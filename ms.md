# МойСклад (MySklad) Integration Service
## 📦 Сервис интеграции с МойСклад Phone API

Сервис для интеграции системы телефонии с МойСклад через Phone API 1.0.
Обеспечивает создание звонков, поиск сотрудников и уведомления в интерфейсе МойСклад.

### 📋 Обзор

**Название сервиса:** `ms.py` (MoySklad)  
**Порт:** `8023`  
**Версия API:** Phone API 1.0  
**Документация API:** [https://dev.moysklad.ru/doc/api/phone/1.0/](https://dev.moysklad.ru/doc/api/phone/1.0/)

### 🔧 Основные возможности

- **🔍 Поиск сотрудников** - по добавочным номерам через Phone API
- **📞 Создание звонков** - автоматическая регистрация звонков в МойСклад
- **🔔 Уведомления** - отображение карточек звонков в интерфейсе МойСклад
- **📤 Исходящие звонки** - попапы только у звонящего сотрудника
- **📱 События звонков** - SHOW/HIDE/STARTTIME для управления уведомлениями
- **🔗 Webhook интеграция** - безопасные UUID-эндпоинты для приема данных
- **🆕 Автоматическое создание клиентов** - при входящих/исходящих звонках от неизвестных номеров

### 🚀 Быстрый старт

```bash
# Запуск сервиса
./ms.sh start

# Проверка статуса
./ms.sh status

# Просмотр логов
./ms.sh logs

# Перезапуск
./ms.sh restart
```

### ⚙️ Конфигурация

#### Основные настройки
- **🔑 Ключ интеграции** - уникальный токен от МойСклад для Phone API 1.0
- **🎯 Токен основного API** - токен для основного API МойСклад 1.2 (получение клиентов, работа с данными)
- **📡 Phone API URL** - `https://api.moysklad.ru/api/phone/1.0` (захардкожен)
- **🔗 Webhook URL** - автогенерируемый безопасный UUID-эндпоинт

#### Настройки уведомлений
- **Уведомления о звонках** - включение/отключение
- **Уведомления входящих** - отдельно для входящих звонков
- **Уведомления исходящих** - отдельно для исходящих звонков

#### Настройки создания заказов
- **Автосоздание заказов** - автоматическое создание заказов при звонках
- **Статус новых заказов** - статус для автоматически созданных заказов
- **Источник заказов** - источник для телефонных заказов

#### 🆕 Настройки автосоздания клиентов
- **Создание клиента при неизвестном звонке** - автоматическое создание контрагентов для новых номеров

### 📡 API Endpoints

#### Админские эндпоинты

```
GET  /ms-admin/?enterprise_number={id}     - Веб-интерфейс админки
GET  /ms-admin/api/config/{enterprise_number} - Получение конфигурации
PUT  /ms-admin/api/config/{enterprise_number} - Обновление конфигурации
GET  /ms-admin/api/test/{enterprise_number}    - Тестирование подключения
GET  /ms.png                              - Логотип МойСклад
GET  /ms-admin/favicon.ico                - Favicon для админки
```

#### Webhook эндпоинты

```
POST /ms/webhook/{uuid}              - Прием webhook от МойСклад (UUID для безопасности)
POST /internal/ms/incoming-call      - События dial (только localhost)
POST /internal/ms/hangup-call        - События hangup (только localhost)
POST /internal/ms/notify-incoming    - Уведомления менеджерам (только localhost)
POST /internal/ms/log-call          - Логирование звонков (только localhost)
```

#### МойСклад Phone API (тестирование)

```
GET  https://api.moysklad.ru/api/phone/1.0/employee           - Список сотрудников
GET  https://api.moysklad.ru/api/phone/1.0/employee?filter=extention=152 - Поиск по номеру
POST https://api.moysklad.ru/api/phone/1.0/call              - Создание звонка
PUT  https://api.moysklad.ru/api/phone/1.0/call/{id}         - Обновление звонка
POST https://api.moysklad.ru/api/phone/1.0/call/{id}/event   - События звонка
```

#### МойСклад Main API (автосоздание клиентов)

```
GET  https://api.moysklad.ru/api/remap/1.2/entity/counterparty - Поиск контрагентов
POST https://api.moysklad.ru/api/remap/1.2/entity/counterparty - Создание контрагентов
```

### 🗂️ Структура данных

#### Конфигурация предприятия
```json
{
  "phone_api_url": "https://api.moysklad.ru/api/phone/1.0",
  "integration_code": "5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763",
  "api_token": "cd5134fa1beec235ed6cc3c4973d4daf540bab8b",
  "webhook_uuid": "b645bb45-2146-46c3-bb54-29a0e83b39a5",
  "webhook_url": "https://bot.vochi.by/ms/webhook/b645bb45-2146-46c3-bb54-29a0e83b39a5",
  "enabled": true,
  "notifications": {
    "call_notify_mode": "during",
    "notify_incoming": true,
    "notify_outgoing": false
  },
  "incoming_call_actions": {
    "create_client": true,
    "create_order": "none",
    "order_source": "Телефонный звонок"
  },
  "employee_mapping": {
    "151": {
      "name": "Тимлидовый С.",
      "email": "e.baevski@gmail.com", 
      "employee_id": "7af8c227-87d4-11f0-0a80-03f20007fada"
    },
    "152": {
      "name": "Баевский Е.",
      "email": "evgeny.baevski@gmail.com",
      "employee_id": "b822ef8f-8649-11f0-0a80-14bb00347cf2"  
    }
  }
}
```

#### Сотрудник МойСклад (Phone API)
```json
{
  "extention": "152",
  "meta": {
    "href": "https://api.moysklad.ru/api/remap/1.2/entity/employee/b822ef8f-8649-11f0-0a80-14bb00347cf2",
    "type": "employee",
    "mediaType": "application/json"
  }
}
```

#### Звонок МойСклад (Phone API)
```json
{
  "meta": {
    "href": "https://api.moysklad.ru/api/phone/1.0/call/ced37d03-87d3-11f0-0a80-05f400004a46",
    "type": "call",
    "mediaType": "application/json"
  },
  "id": "ced37d03-87d3-11f0-0a80-05f400004a46",
  "externalId": "test-call-1756800450",
  "number": "+375297111222",
  "extension": "152",
  "employee": {
    "href": "https://api.moysklad.ru/api/remap/1.2/entity/employee/b822ef8f-8649-11f0-0a80-14bb00347cf2",
    "type": "employee",
    "mediaType": "application/json"
  },
  "isIncoming": true,
  "startTime": "2025-09-02 11:07:30.000",
  "endTime": null,
  "duration": null,
  "recordUrl": [],
  "comment": "Test call from МойСклад webhook"
}
```

### 🔄 Рабочий процесс

#### 1. Входящий звонок
1. Получение звонка через Asterisk
2. Поиск клиента в МойСклад по номеру телефона
3. **🆕 Если клиент не найден + `create_client: true` → автоматическое создание контрагента**
4. Если клиент найден - отображение ФИО на телефоне
5. Создание заказа если настроено
6. Отправка уведомления ответственному менеджеру

#### 2. Исходящий звонок
1. Получение данных о звонке
2. Поиск клиента в МойСклад
3. Логирование звонка
4. Отправка уведомления если настроено

### 🐛 Логирование и отладка

#### Файлы логов
- `/root/asterisk-webhook/logs/ms.log` - основной лог сервиса
- `/root/asterisk-webhook/logs/integration_cache.log` - логи интеграций

#### Уровни логирования
- `INFO` - основная информация о работе
- `WARNING` - предупреждения
- `ERROR` - ошибки
- `DEBUG` - детальная отладочная информация

### 🔧 Администрирование

#### Web-интерфейс
Доступ к админке: `http://ваш-сервер:8023/ms/`

#### Настройка через web-интерфейс
1. Перейдите в админку
2. Выберите предприятие
3. Настройте параметры подключения к МойСклад
4. Настройте правила обработки звонков
5. Сохраните изменения

#### Проверка подключения
```bash
# Через web-интерфейс
curl https://bot.vochi.by/ms-admin/api/test/0367

# Прямое тестирование Phone API
curl -s "https://api.moysklad.ru/api/phone/1.0/employee" \
  -H "Lognex-Phone-Auth-Token: ВАШИХ_КЛЮЧ_ИНТЕГРАЦИИ" \
  -H "Accept-Encoding: gzip" \
  -H "Content-Type: application/json" \
  --compressed | jq .
```

### 📊 Мониторинг

#### Метрики
- Количество обработанных звонков
- Время отклика API МойСклад
- Процент успешных запросов
- Количество созданных заказов
- **🆕 Количество автоматически созданных клиентов**

#### Журнал событий
Все события интеграции логируются в таблицу `integration_logs`:
```sql
SELECT * FROM integration_logs
WHERE integration_type = 'ms'
ORDER BY created_at DESC
LIMIT 50;
```

### 🆘 Устранение неисправностей

#### Распространенные проблемы

**1. Ошибка аутентификации**
```
Решение: Проверьте логин и пароль в настройках
```

**2. Таймаут подключения**
```
Решение: Проверьте доступность api.moysklad.ru
```

**3. Ошибка создания заказа**
```
Решение: Проверьте права доступа пользователя в МойСклад
```

#### Диагностика
```bash
# Проверка статуса сервиса
./ms.sh status

# Просмотр последних ошибок
./ms.sh logs | tail -20

# Тестирование подключения
curl -X POST http://localhost:8023/ms-admin/test/0367
```

### 🔄 Обновления

#### Версии
- **v1.0** - Базовая интеграция с МойСклад
- **v1.1** - Добавлена поддержка заказов
- **v1.2** - Улучшена обработка ошибок
- **v1.3** - **🆕 Добавлено автоматическое создание клиентов (2024-12-19)**
- **v2.0** - **📤 Добавлена поддержка исходящих звонков с настраиваемыми попапами и автосозданием клиентов (2025-09-05)**

#### Совместимость
- МойСклад API v1.2+
- Python 3.8+
- FastAPI
- PostgreSQL

---

## ✅ **РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ PHONE API (2025-09-02)**

**Использованы два токена**:
- **🔑 Ключ интеграции**: `5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763` (Phone API 1.0)
- **🎯 Токен основного API**: `cd5134fa1beec235ed6cc3c4973d4daf540bab8b` (основной API 1.2)

### **🧪 Проведенные тесты:**

#### **1. 👥 Получение списка сотрудников**
```bash
curl -s "https://api.moysklad.ru/api/phone/1.0/employee" \
  -H "Lognex-Phone-Auth-Token: 5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763" \
  -H "Accept-Encoding: gzip" --compressed
```
**Результат:** ✅ Успешно получен список сотрудников с добавочными номерами

#### **2. 🔍 Поиск сотрудников по добавочному номеру**
```bash
curl -s "https://api.moysklad.ru/api/phone/1.0/employee?filter=extention~=15" \
  -H "Lognex-Phone-Auth-Token: 5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763" \
  --compressed | jq .
```
**Результат:** ✅ Найден сотрудник с номером "152"

#### **3. 📞 Создание тестового звонка**
```bash
curl -s "https://api.moysklad.ru/api/phone/1.0/call" \
  -X POST \
  -H "Lognex-Phone-Auth-Token: 5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763" \
  -d '{"externalId":"test-call-1756800450","number":"+375297111222","extension":"152","isIncoming":true,"startTime":"2025-09-02 11:07:30.000","comment":"Test call from МойСклад webhook"}'
```
**Результат:** ✅ Звонок создан с ID `ced37d03-87d3-11f0-0a80-05f400004a46`

#### **4. 🎯 Точный поиск сотрудника**
```bash
curl -s "https://api.moysklad.ru/api/phone/1.0/employee?filter=extention=152" \
  -H "Lognex-Phone-Auth-Token: 5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763" \
  --compressed | jq .
```
**Результат:** ✅ Точное совпадение по номеру 152

### **📊 Выводы тестирования:**

- **🔐 Аутентификация:** Работает через `Lognex-Phone-Auth-Token`
- **📱 API функциональность:** Полный доступ к сотрудникам и звонкам
- **🔍 Фильтрация:** Поддерживается поиск по добавочным номерам
- **📞 Создание звонков:** Успешно с привязкой к сотрудникам
- **🌐 Доступность:** API стабильно отвечает

---

## 🏗️ TO-DO: Реализация интеграции с МойСклад Phone API

### **📋 АНАЛИЗ ТРЕБОВАНИЙ:**

#### **1. Поля конфигурации в админке:**
- ✅ **МойСклад Phone API URL** - `https://api.moysklad.ru/api/phone/1.0` (захардкожен)
- ✅ **Ключ интеграции** - уникальный токен от МойСклад для каждого юнита
- ✅ **Webhook URL** - безопасный UUID-эндпоинт для копипаста в МойСклад

#### **2. Webhook эндпоинты для МойСклад:**

**Исходящий (для copy-paste в МойСклад):**
```
https://bot.vochi.by/ms/webhook/{uuid}
Пример: https://bot.vochi.by/ms/webhook/b645bb45-2146-46c3-bb54-29a0e83b39a5
```

**Внутренние (для integration_cache):**
```
POST /internal/ms/call-event        - события dial/hangup
POST /internal/ms/notify-incoming   - уведомления
POST /internal/ms/log-call         - логирование звонков
```

### **📝 ПЛАН РЕАЛИЗАЦИИ:**

#### **Этап 1: Админка интеграции**
- ✅ **ms-admin.html** - UI админки по аналогии с RetailCRM/UON
- ✅ **Поля конфигурации:**
  - ✅ Phone API URL (захардкожен `https://api.moysklad.ru/api/phone/1.0`)
  - ✅ Ключ интеграции (input field)
  - ✅ Включен/отключен (checkbox)
- ✅ **Webhook URL генерация** - безопасный UUID для каждого юнита
- ✅ **Тестирование подключения** - проверка доступности Phone API

#### **Этап 2: Webhook эндпоинты**
- ✅ **POST /ms/webhook/{uuid}** - прием webhook от МойСклад
- ✅ **POST /internal/ms/call-event** - для integration_cache (dial/hangup)
- ✅ **POST /internal/ms/notify-incoming** - уведомления менеджерам
- ✅ **POST /internal/ms/log-call** - логирование в МойСклад

#### **Этап 3: Интеграция с Phone API**
- ✅ **Авторизация** - `Lognex-Phone-Auth-Token` через ключ интеграции
- ✅ **Поиск сотрудников** - по добавочным номерам (`filter=extention=152`)
- ✅ **Создание звонков** - регистрация в МойСклад
- ✅ **Тестирование API** - все эндпоинты проверены и работают

#### **Этап 4: Integration Cache**
- [ ] **Добавить ms в кэш** - проверка активности интеграции
- [ ] **Роутинг событий** - dispatch в ms.py при активной интеграции
- [ ] **Primary integration** - поддержка МойСклад как основной

#### **Этап 5: Smart Redirect**
- [ ] **customer-by-phone** - поиск клиента в МойСклад
- [ ] **responsible-extension** - определение ответственного менеджера
- [ ] **algorithm="ms"** - поддержка в smart.py

### **🔗 WEBHOOK URL СТРУКТУРА:**

**Аналогия с существующими:**
```
RetailCRM: GET  /retailcrm/make-call?clientId={token}&...
U-ON:      POST /uon/webhook
МойСклад:  POST /ms/webhook/{enterprise_number}
```

**Пример сгенерированного URL для копипаста:**
```
https://bot.vochi.by/ms/webhook/0367
```

### **💾 БАЗА ДАННЫХ:**

**Конфигурация в `enterprises.integrations_config`:**
```json
{
  "ms": {
    "enabled": true,
    "phone_api_url": "https://api.moysklad.ru/api/phone/1.0",
    "integration_code": "5a5aeff50b6d9fe40d3820d4aef5fe2b7e2bd763",
    "api_token": "cd5134fa1beec235ed6cc3c4973d4daf540bab8b",
    "webhook_uuid": "b645bb45-2146-46c3-bb54-29a0e83b39a5",
    "webhook_url": "https://bot.vochi.by/ms/webhook/b645bb45-2146-46c3-bb54-29a0e83b39a5",
    "notifications": {
      "call_notify_mode": "during",
      "notify_incoming": true,
      "notify_outgoing": false
    },
    "incoming_call_actions": {
      "create_customers": true,
      "create_orders": false
    }
  }
}
```

---

## 📤 **ИСХОДЯЩИЕ ЗВОНКИ (2025-09-05)**

### **📋 Описание функционала**

Интеграция поддерживает обработку исходящих звонков с настраиваемыми опциями уведомлений и автосоздания клиентов.

### **🔧 Ключевые особенности:**

- **🎯 Попап только у звонящего** - карточка отображается только сотруднику, который совершает звонок
- **📞 Определение звонящего** - по полю `CallerIDNum` из данных звонка
- **⚙️ Настраиваемые уведомления** - можно отключить/включить отдельно от входящих
- **🆕 Автосоздание клиентов** - опционально создавать новых клиентов при звонках на неизвестные номера

### **🔄 Логика работы:**

1. **Получение события** - `dial` с `direction: "out"` от `integration_cache.py`
2. **Проверка настроек** - `notifications.notify_outgoing` в конфигурации предприятия
3. **Определение звонящего** - извлечение внутреннего номера из `CallerIDNum`
4. **Поиск/создание контакта** - если включено `outgoing_call_actions.create_client`
5. **Создание звонка** - в МойСклад от конкретного сотрудника
6. **Отправка попапа** - ТОЛЬКО звонящему сотруднику

### **⚙️ Настройки в админке:**

В секции "Действия при исходящем звонке":
- **Создание клиента при неизвестном номере** - автосоздание клиентов
- **Создание заказа** - автоматическое создание заказов (опционально)
- **Источник заказа** - метка для созданных заказов (по умолчанию: "Исходящий звонок")

В секции "Уведомления":
- **Уведомлять при исходящем** - включение/отключение попапов

### **📊 Пример конфигурации:**

```json
{
  "notifications": {
    "notify_outgoing": true
  },
  "outgoing_call_actions": {
    "create_client": true,
    "create_order": "none",
    "order_source": "Исходящий звонок"
  }
}
```

### **📈 Результаты тестирования:**

```bash
# Тест исходящего звонка с автосозданием клиента
📞 Outgoing call from extension 151 to +375447777888
🆕 Creating new customer for phone: +375447777888
✅ Customer created successfully: b9ba147b-8a3a-11f0-0a80-15c800160df
✅ МойСклад outgoing popup sent ONLY to calling extension 151

# Тест с отключенными уведомлениями
ℹ️ Outgoing call notifications disabled for enterprise 0367
```

### **🎉 Статус функционала**

- ✅ **Интерфейс настроек готов** - секция для исходящих звонков в админке
- ✅ **Логика обработки реализована** - `process_ms_outgoing_call()`
- ✅ **Автосоздание клиентов работает** - для исходящих звонков
- ✅ **Попапы только у звонящего** - корректная логика отображения
- ✅ **Настройки сохраняются** - в `integrations_config.ms.outgoing_call_actions`
- ✅ **Тестирование завершено** - все сценарии работают правильно

## 🎉 **АВТОМАТИЧЕСКОЕ СОЗДАНИЕ КЛИЕНТОВ (2024-12-19)**

### **📋 Описание функционала**

Реализована возможность автоматического создания контрагентов в МойСклад при входящих звонках от неизвестных номеров.

### **🔧 Техническая реализация**

#### **Новые функции в `ms.py`:**

1. **`find_or_create_contact(phone, auto_create, ms_config)`** (строки 1762-1816)
   - Унифицированный поиск и создание контактов
   - Поддержка Bearer token для обоих API
   - Graceful fallback при ошибках

2. **`create_customer(customer_data, api_token, api_url)`** (строки 349-392)
   - Переработана для использования Bearer token
   - Правильные заголовки для МойСклад Main API
   - Подробное логирование

#### **Интеграция в обработку звонков:**

Модификация `process_ms_incoming_call()` (строки 2057-2067):
```python
# Получаем настройку автосоздания клиентов
auto_create = ms_config.get('incoming_call_actions', {}).get('create_client', False)
logger.info(f"🔧 Auto-create setting: {auto_create}")

# Используем новую функцию с автосозданием
contact_info = await find_or_create_contact(phone, auto_create, ms_config)
```

### **🎯 Логика работы**

```
Входящий звонок от неизвестного номера
       ↓
1. Поиск контакта по номеру телефона (Bearer token)
       ↓
2. Контакт найден? 
   ├─ ДА → Использовать существующего
   └─ НЕТ → Проверить настройку create_client
              ↓
         3. create_client = true?
            ├─ ДА → Создать нового клиента через Main API
            │       ↓  
            │   4. Создать контрагента в МойСклад
            │       ↓  
            │   5. Использовать созданного клиента
            │
            └─ НЕТ → Продолжить без контакта
                     ↓
6. Создать звонок в МойСклад Phone API
```

### **📊 Тестирование и результаты**

#### **✅ Успешное автосоздание клиента:**

**Тестовый запрос:**
```bash
curl -X POST "http://localhost:8023/internal/ms/incoming-call" \
-d '{"phone": "+375447777781", "ms_config": {"integration_code": "test", "api_token": "cd5134fa1beec235ed6cc3c4973d4daf540bab8b", "incoming_call_actions": {"create_client": true}}}'
```

**Результат в логах:**
```
🔧 Auto-create setting: true
🔍 Searching for contact with phone: +375447777781
📞 Contact search response: status=200
📋 Found 0 contacts
⚠️ No contacts found for phone +375447777781
🆕 Creating new customer for phone: +375447777781
🆕 Creating customer via Main API: +375447777781 (+375447777781)
📞 Create customer response: status=200
✅ Customer created successfully: 31ec22dd-8a2e-11f0-0a80-0fde00140dfb
✅ Successfully created customer 31ec22dd-8a2e-11f0-0a80-0fde00140dfb for phone +375447777781
```

### **⚙️ Настройка**

#### **В админке МойСклад:**
1. Перейти в настройки интеграции МойСклад
2. Установить чекбокс **"Создание клиента при неизвестном звонке"**
3. Сохранить настройки

#### **В конфигурации:**
```json
{
  "incoming_call_actions": {
    "create_client": true,
    "create_order": "none",
    "order_source": "Телефонный звонок"
  }
}
```

### **🔍 Мониторинг**

#### **Логи автосоздания:**
```bash
# Поиск логов автосоздания клиентов
grep -i "Creating.*customer\|Customer.*created\|auto-create" logs/ms.log

# Поиск созданных клиентов
grep "Customer created successfully" logs/ms.log
```

#### **Параметры созданных клиентов:**
- **Название:** Номер телефона (например: `"+375447777781"`)
- **Телефон:** Номер звонившего
- **Email:** Пустой
- **Теги:** `["Создан автоматически"]`

### **🎉 Статус функционала**

- ✅ **Функционал реализован на 100%**
- ✅ **Тестирование завершено успешно**
- ✅ **Готов к продакшену**
- ✅ **Логирование и мониторинг настроены**

**Автосоздание клиентов работает и готово к использованию!** 🚀

---

*Документация создана: 2025-01-31*  
*Автор: AI Assistant*  
*Версия: 2.2*  
*Обновлено: 2025-09-02 - добавлены результаты тестирования Phone API, поддержка токена основного API 1.2, обновлена структура данных*
*Обновлено: 2024-12-19 - добавлено автоматическое создание клиентов, полное тестирование функционала*
