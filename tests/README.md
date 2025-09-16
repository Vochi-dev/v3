# Тестовый фреймворк для системы фильтрации событий Asterisk

Комплексная система тестирования для проверки работы фильтрации событий звонков.

## Структура

```
tests/
├── call_emulator/          # Эмуляторы звонков
│   ├── api_server.py      # REST API для эмуляции событий
│   └── emulator.py        # Интерактивный эмулятор звонков
├── event_templates/        # Шаблоны событий в JSON
│   ├── simple_incoming_call.json
│   ├── simple_outgoing_call.json
│   ├── transfer_call.json
│   ├── followme_call.json
│   └── busy_manager_call.json
└── test_reports/          # Отчеты тестирования
```

## Использование

### 1. API Сервер для эмуляции событий

```bash
# Запуск API сервера
cd tests/call_emulator
python api_server.py

# Отправка одиночного события
curl -X POST http://localhost:5000/send_event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "start",
    "data": {"UniqueId": "123", "Phone": "375296254070"},
    "endpoint": "/start"
  }'

# Выполнение готового паттерна
curl -X POST http://localhost:5000/execute_pattern/simple_incoming
```

### 2. Интерактивный эмулятор

```bash
cd tests/call_emulator
python emulator.py
```

Меню опций:
- 1. Простой входящий звонок
- 2. Простой исходящий звонок  
- 3. Перевод звонка
- 4. FollowMe переадресация
- 5. Звонок занятому менеджеру
- 6. Полный набор тестов
- 7. Стресс-тест

### 3. Использование шаблонов

Шаблоны в `event_templates/` содержат:
- Последовательность событий
- Задержки между событиями
- Ожидаемые результаты фильтрации

```python
import json

# Загрузка шаблона
with open('tests/event_templates/simple_incoming_call.json') as f:
    template = json.load(f)

# Выполнение последовательности
for event in template['sequence']:
    await send_event(event['endpoint'], event['data'])
    await asyncio.sleep(event['delay'])
```

## Типы тестируемых звонков

### Простые звонки
- **simple_incoming**: Входящий звонок (start → dial → bridge → hangup)
- **simple_outgoing**: Исходящий звонок (dial → bridge → hangup)

### Сложные сценарии
- **transfer**: Перевод звонка с множественными мостами
- **followme**: FollowMe переадресация на несколько номеров
- **busy_manager**: Звонок занятому менеджеру

## Конфигурация

Параметры в `call_emulator/emulator.py`:

```python
config = CallEmulatorConfig(
    target_host="localhost",      # Хост основного сервиса
    target_port=8001,            # Порт основного сервиса
    enterprise_token="375293332255",  # Токен предприятия
    delay_between_events=0.5,    # Задержка между событиями
    delay_between_calls=2.0      # Задержка между звонками
)
```

## Проверка результатов

Каждый шаблон содержит `expected_results` для проверки:

```json
"expected_results": {
  "bitrix24_events": 3,    # Ожидаемое количество событий для Bitrix24
  "telegram_events": 2,    # Ожидаемое количество событий для Telegram
  "complexity": "SIMPLE",  # Ожидаемый тип сложности
  "primary_uid": "1757772742.60"  # Основной UniqueId
}
```

## Логирование

Эмулятор выводит подробные логи:
- 📤 Отправленные события
- ✅ Успешные тесты
- ❌ Ошибки
- 📋 Статистика выполнения

## Интеграция с основным сервисом

Эмулятор отправляет события на эндпоинты основного сервиса:
- `/start` - начало звонка
- `/dial` - набор номера
- `/bridge` - соединение
- `/hangup` - завершение

Убедитесь, что основной сервис запущен на указанном хосте и порту.


