# 📊 Call Logger Service - Документация

**Дата обновления:** 30.11.2025 16:00  
**Версия:** 2.1 (файловое логирование + HTTP логи)

---

## 🎯 Назначение

Централизованный сервис для:
1. **Логирования событий звонков** - все события Asterisk и Telegram записываются в файлы
2. **Просмотра деталей звонка** - HTML страница с полной информацией о звонке

---

## 🏗️ Архитектура

### Компоненты системы

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────────┐
│  Asterisk Host  │────▶│    main.py       │────▶│  call_tracer/           │
│  (10.88.10.xx)  │     │    (port 8000)   │     │  └── {enterprise}/      │
└─────────────────┘     └──────────────────┘     │      └── events.log     │
                               │                 └─────────────────────────┘
                               ▼
                        ┌──────────────────┐
                        │  Обработчики:    │
                        │  start.py        │
                        │  dial.py         │
                        │  bridge.py       │
                        │  hangup.py       │
                        │  internal.py     │
                        └──────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Telegram Bot    │
                        │  (отправка msg)  │
                        └──────────────────┘

┌─────────────────┐     ┌──────────────────┐
│  Telegram кнопка│────▶│    logger.py     │────▶ HTML страница
│  "Детали звонка"│     │    (port 8026)   │      (из файлов)
└─────────────────┘     └──────────────────┘
```

---

## 📁 Структура файлов логов

### Расположение
```
/root/asterisk-webhook/call_tracer/
├── 0103/
│   ├── events.log           # Текущий файл
│   └── events.log.2025-11-29  # Ротированный файл
├── 0113/
│   ├── events.log
│   └── events.log.2025-11-29
├── 0367/
│   ├── events.log
│   └── events.log.2025-11-29
└── ... (по папке для каждого юнита)
```

### Автоматическое создание
- Папки создаются автоматически при первом событии от юнита
- Файлы `events.log` создаются автоматически

### Ротация файлов
- **Когда:** При первом событии после полуночи (UTC)
- **Имя:** `events.log.YYYY-MM-DD` (дата предыдущего дня)
- **Хранение:** 14 дней (backupCount=14)
- **Кодировка:** UTF-8

---

## 📐 Формат записей

### Asterisk события (AST)
```
TIMESTAMP|AST|EVENT_TYPE|UNIQUE_ID|JSON_BODY
```

**Пример:**
```
2025-11-30 09:08:42,025|AST|hangup|1764493655.61|{"CallStatus":"0","Phone":"375295305942",...}
```

| Поле | Описание | Пример |
|------|----------|--------|
| TIMESTAMP | Время получения | `2025-11-30 09:08:42,025` |
| AST | Маркер Asterisk | `AST` |
| EVENT_TYPE | Тип события | `start`, `dial`, `bridge`, `hangup`, `bridge_leave`, `bridge_create` |
| UNIQUE_ID | Asterisk UniqueId | `1764493655.61` |
| JSON_BODY | Полные данные события | `{...}` |

### Telegram события (TG)
```
TIMESTAMP|TG|ACTION|CHAT_ID|MSG_TYPE|MSG_ID|UNIQUE_ID|TEXT
```

**Пример:**
```
2025-11-30 09:08:44,283|TG|send|374573193|hangup|63157|1764493655.61|❌ Абонент не поднял трубку...
```

| Поле | Описание | Пример |
|------|----------|--------|
| TIMESTAMP | Время события | `2025-11-30 09:08:44,283` |
| TG | Маркер Telegram | `TG` |
| ACTION | Действие | `send`, `edit`, `delete` |
| CHAT_ID | ID получателя | `374573193` |
| MSG_TYPE | Тип сообщения | `start`, `dial`, `bridge`, `hangup` |
| MSG_ID | ID сообщения в TG | `63157` |
| UNIQUE_ID | Asterisk UniqueId | `1764493655.61` |
| TEXT | Текст (до 1000 символов) | `❌ Абонент не поднял трубку...` |


### HTTP запросы (HTTP)
```
TIMESTAMP|HTTP|UNIQUE_ID|METHOD|URL|STATUS_CODE|REQUEST_JSON|RESPONSE_JSON
```

**Пример:**
```
2025-11-30 12:00:01,300|HTTP|1764500001.5|GET|http://localhost:8020/customer-name/0367/375296254070|200|{"phone":"375296254070"}|{"name":"Тестовый покупатель"}
```

| Поле | Описание | Пример |
|------|----------|--------|
| TIMESTAMP | Время запроса | `2025-11-30 12:00:01,300` |
| HTTP | Маркер HTTP | `HTTP` |
| UNIQUE_ID | Asterisk UniqueId | `1764500001.5` |
| METHOD | HTTP метод | `GET`, `POST` |
| URL | URL запроса | `http://localhost:8020/customer-name/...` |
| STATUS_CODE | Код ответа | `200`, `404` |
| REQUEST_JSON | Параметры запроса | `{"phone":"..."}` |
| RESPONSE_JSON | Ответ (до 500 символов) | `{"name":"..."}` |
---

## 📞 Полный пример звонка

```
# 1. Asterisk: начало звонка (dial)
2025-11-30 12:00:01,100|AST|dial|1764500001.5|{"Phone":"375296254070","Exten":"151","CallType":1,...}

# 2. Telegram: отправка dial каждому менеджеру
2025-11-30 12:00:01,250|TG|send|374573193|dial|45001|1764500001.5|📞 Исходящий звонок ☎️151 ➡️ 💰+375296254070
2025-11-30 12:00:01,280|TG|send|7889254605|dial|45002|1764500001.5|📞 Исходящий звонок ☎️151 ➡️ 💰+375296254070

# 3. Asterisk: соединение (bridge)
2025-11-30 12:00:15,500|AST|bridge|1764500001.5|{"Phone":"375296254070","BridgeUniqueid":"...",...}

# 4. Telegram: удаление dial + отправка bridge
2025-11-30 12:00:15,600|TG|delete|374573193|dial|45001|1764500001.5|
2025-11-30 12:00:15,650|TG|send|374573193|bridge|45010|1764500001.5|☎️151 📞➡️ 💰+375296254070 Клиент: Иван Петров

# 5. Asterisk: завершение (hangup)
2025-11-30 12:02:30,000|AST|hangup|1764500001.5|{"Phone":"375296254070","CallStatus":"2",...}

# 6. Telegram: удаление bridge + финальное сообщение
2025-11-30 12:02:30,100|TG|delete|374573193|bridge|45010|1764500001.5|
2025-11-30 12:02:30,150|TG|send|374573193|hangup|45020|1764500001.5|✅Успешный звонок 💰+375296254070 ⏱02:15
```

---

## 🔧 Технические характеристики

### Сервис logger.py
- **Порт:** 8026
- **Назначение:** Генерация HTML страницы с деталями звонка
- **Источник данных:** Файлы `call_tracer/{enterprise}/events.log*`
- **Авторизация:** По secret токену предприятия

### Сервис main.py
- **Порт:** 8000
- **Назначение:** Приём событий от Asterisk хостов, запись в файлы
- **Workers:** 2 (uvicorn)

---

## 🌐 API Endpoints

### `GET /call/{enterprise_number}/{unique_id}?token=SECRET`

Генерирует HTML страницу с деталями звонка.

**Параметры:**
- `enterprise_number` - номер предприятия (0367)
- `unique_id` - Asterisk UniqueId звонка
- `token` - secret токен из таблицы enterprises

**Как работает:**
1. Проверяет токен в БД
2. Читает все файлы `call_tracer/{enterprise}/events.log*`
3. Ищет строки с `unique_id`
4. Парсит AST и TG события
5. Обогащает имена через metadata сервис (8020)
6. Рендерит HTML шаблон `call_details.html`

**Пример:**
```
GET http://localhost:8026/call/0367/1764493655.61?token=698c81fe16124c2e805f3a3a2ddedae0
```

### `GET /health`

Проверка здоровья сервиса.

---

## 📝 Код логирования

### Модуль `app/utils/call_tracer.py`

```python
import logging
import os
import json
from logging.handlers import TimedRotatingFileHandler
from typing import Dict

_call_tracer_loggers: Dict[str, logging.Logger] = {}

def get_call_tracer_logger(enterprise_number: str) -> logging.Logger:
    """Возвращает логгер для юнита, создаёт папку и файл при необходимости."""
    if enterprise_number in _call_tracer_loggers:
        return _call_tracer_loggers[enterprise_number]
    
    log_dir = f"call_tracer/{enterprise_number}"
    os.makedirs(log_dir, exist_ok=True)
    
    handler = TimedRotatingFileHandler(
        f"{log_dir}/events.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s|%(message)s"))
    handler.suffix = "%Y-%m-%d"
    
    tracer_logger = logging.getLogger(f"call_tracer_{enterprise_number}")
    tracer_logger.addHandler(handler)
    tracer_logger.setLevel(logging.INFO)
    tracer_logger.propagate = False
    
    _call_tracer_loggers[enterprise_number] = tracer_logger
    return tracer_logger


def log_telegram_event(
    enterprise_number: str,
    action: str,           # send, edit, delete
    chat_id: int,
    message_type: str,     # start, dial, bridge, hangup
    message_id: int,
    unique_id: str,
    text: str = ""
):
    """Логирует Telegram событие."""
    try:
        if not enterprise_number:
            return
        tracer = get_call_tracer_logger(enterprise_number)
        text_truncated = text[:200].replace('\n', ' ').replace('\r', '') if text else ""
        tracer.info(f"TG|{action}|{chat_id}|{message_type}|{message_id}|{unique_id}|{text_truncated}")
    except Exception as e:
        logging.warning(f"Failed to log telegram event: {e}")


def log_asterisk_event(
    enterprise_number: str,
    event_type: str,
    unique_id: str,
    body: dict
):
    """Логирует Asterisk событие."""
    try:
        if not enterprise_number:
            return
        tracer = get_call_tracer_logger(enterprise_number)
        tracer.info(f"AST|{event_type}|{unique_id}|{json.dumps(body, ensure_ascii=False)}")
    except Exception as e:
        logging.warning(f"Failed to log Asterisk event: {e}")
```

### Использование в main.py

```python
from app.utils.call_tracer import log_asterisk_event

async def _dispatch_to_all(token: str, event_type: str, unique_id: str, body: dict):
    enterprise_number = await _get_enterprise_number_by_token(token)
    if enterprise_number:
        await log_asterisk_event(enterprise_number, event_type, unique_id, body)
        body["_enterprise_number"] = enterprise_number  # Для обработчиков
    # ... дальнейшая обработка
```

### Использование в обработчиках

```python
from app.utils.call_tracer import log_telegram_event

# После отправки сообщения:
sent = await bot.send_message(chat_id, text)
log_telegram_event(
    enterprise_number=data.get("_enterprise_number", ""),
    action="send",
    chat_id=chat_id,
    message_type="dial",
    message_id=sent.message_id,
    unique_id=uid,
    text=text
)

# После удаления сообщения:
await bot.delete_message(chat_id, message_id)
log_telegram_event(
    enterprise_number=data.get("_enterprise_number", ""),
    action="delete",
    chat_id=chat_id,
    message_type="dial",
    message_id=message_id,
    unique_id=uid
)
```

---

## 👤 Данные пользователей

В логах хранится только `chat_id`. Имя и email подтягиваются из БД при формировании HTML:

```sql
SELECT first_name, last_name, email 
FROM telegram_users 
WHERE tg_id = $1
```

---

## 🔄 Управление сервисом

### Запуск/остановка
```bash
./logger.sh start
./logger.sh stop
./logger.sh restart
```

### Проверка здоровья
```bash
curl http://localhost:8026/health
```

### Просмотр логов
```bash
tail -f logs/logger.log
```

### Просмотр событий юнита
```bash
tail -f call_tracer/0367/events.log
```

---

## ⚠️ Важные замечания

1. **PostgreSQL больше НЕ используется** для хранения событий звонков. Таблица `call_traces` устарела.

2. **Файлы логов** - единственный источник данных для страницы деталей звонка.

3. **Ротация** происходит по UTC времени, файлы именуются датой предыдущего дня.

4. **Хранение 14 дней** - старые файлы автоматически удаляются.

5. **Обогащение данных** - имена клиентов и менеджеров подтягиваются через сервис metadata (8020).

---

**Автор:** Claude (Cursor AI)  
**Для:** Евгений  
**Последнее обновление:** 30.11.2025
