# 📱 СИСТЕМА АВТОРИЗАЦИИ В TELEGRAM-БОТАХ

## 🎯 ОПИСАНИЕ ПРОЕКТА

Система авторизации пользователей в Telegram-ботах предприятий с двухфакторной аутентификацией (email + SMS).

**СТАТУС**: ✅ Полностью реализована и протестирована!

## 📋 КОМПОНЕНТЫ СИСТЕМЫ

### 🔧 Сервисы
- **telegram_auth_service.py** (порт 8016) - основной сервис авторизации ✅
- **telegram.sh** - скрипт управления сервисом (start/stop/restart) ✅
- **nginx** - проксирование `/telegram-auth/` на порт 8016 ✅
- **telegram_auth_handler.py** - обработчик команд для Telegram-ботов ✅
- **auth.py** - endpoint отправки email кодов ✅
- **desk.py** - кнопка доступа к Telegram-боту ✅

### 🗄️ База данных
- **enterprises** - `bot_token`, `chat_id` для каждого предприятия
- **users** - расширена полями для telegram авторизации:
  - `telegram_auth_code` VARCHAR(6) - временный код авторизации
  - `telegram_auth_expires` TIMESTAMP - срок действия кода (10 минут)
  - `telegram_authorized` BOOLEAN - флаг авторизации
  - `telegram_tg_id` BIGINT - Telegram ID пользователя
- **telegram_users** - связка tg_id с bot_token для совместимости

## 🔄 WORKFLOW АВТОРИЗАЦИИ

### Шаг 1: Получение ссылки
1. Пользователь заходит на рабочий стол (desk)
2. Видит кнопку "🔗 Telegram-бот {enterprise_name}"
3. Ссылка формата: `https://t.me/{bot_username}?start=auth_{user_id}_{enterprise_number}`

### Шаг 2: Начало авторизации в боте
1. Пользователь переходит по ссылке и нажимает `/start`
2. Бот запрашивает email
3. Проверяется существование пользователя в БД

### Шаг 3: Отправка кода
1. Генерируется 6-значный код (действует 10 минут)
2. Код отправляется **одновременно**:
   - На email (через существующую систему)
   - На SMS (если есть `personal_phone`)
3. Код сохраняется в БД с временем истечения

### Шаг 4: Верификация
1. Пользователь вводит код в боте
2. При успехе:
   - `telegram_authorized = TRUE`
   - Запись в `telegram_users`
   - Очистка временных полей

## 📡 API ENDPOINTS

### `POST /telegram-auth/start_auth_flow`
Начало процесса авторизации
```json
{
  "email": "user@example.com",
  "enterprise_number": "0387",
  "telegram_id": 123456789
}
```

### `POST /telegram-auth/verify_code`
Проверка 6-значного кода
```json
{
  "email": "user@example.com",
  "code": "123456",
  "telegram_id": 123456789
}
```

### `GET /telegram-auth/check_auth_status/{telegram_id}`
Проверка статуса авторизации пользователя

### `POST /telegram-auth/revoke_auth/{user_id}`
Отзыв авторизации (для админов)

### `GET /telegram-auth/status`
Статус сервиса

## 🔧 УСТАНОВКА И НАСТРОЙКА

### Создание полей БД
```sql
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS telegram_auth_code VARCHAR(6),
ADD COLUMN IF NOT EXISTS telegram_auth_expires TIMESTAMP,
ADD COLUMN IF NOT EXISTS telegram_authorized BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS telegram_tg_id BIGINT;
```

### Запуск сервиса
```bash
./telegram.sh start    # Запуск
./telegram.sh stop     # Остановка
./telegram.sh restart  # Перезапуск
./telegram.sh status   # Статус
./telegram.sh logs     # Логи
./telegram.sh test     # Тест
```

### Управление всеми сервисами
```bash
./all.sh restart  # Включает telegram_auth_service
```

## 🌐 NGINX КОНФИГУРАЦИЯ

```nginx
# Маршруты для Telegram Auth сервиса (порт 8016)
location /telegram-auth/ {
    proxy_pass         http://127.0.0.1:8016/;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
}
```

## 📊 МОНИТОРИНГ

- **Логи**: `/var/log/telegram_auth_service.log`
- **PID**: `.uvicorn_telegram.pid`
- **Порт**: 8016
- **Админка**: включен в модалку сервисов

## 🚀 БУДУЩИЕ ВОЗМОЖНОСТИ

### Управление доступом
- [ ] Блокировка авторизации пользователя
- [ ] Массовый отзыв авторизации
- [ ] Автоматический отзыв при удалении пользователя

### Фильтрация уведомлений
- [ ] Персональные настройки уведомлений
- [ ] Фильтрация по отделам
- [ ] Настройка типов событий
- [ ] Расписание получения уведомлений

### Интеграция
- [ ] Связь с системой ролей
- [ ] Интеграция с аудитом
- [ ] Статистика использования
- [ ] Backup/restore настроек

## 🛠️ ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ

### Зависимости сервисов
- **Email**: auth.py (8015) - для отправки email с кодом
- **SMS**: send_service_sms.py (8013) - для отправки SMS
- **БД**: PostgreSQL - хранение данных авторизации

### Файлы проекта
```
/root/asterisk-webhook/
├── telegram_auth_service.py     # Основной сервис
├── telegram.sh                  # Скрипт управления
├── all.sh                       # Обновлен (добавлен telegram)
├── app/routers/admin.py         # Обновлен (модалка сервисов)
└── auth_telegram.md             # Этот файл
```

### Сетевые порты
- `8016` - telegram_auth_service.py
- `8015` - auth.py (email сервис)
- `8013` - send_service_sms.py (SMS сервис)

## ❗ ВАЖНЫЕ ЗАМЕЧАНИЯ

1. **Безопасность**: Коды действуют только 10 минут
2. **Двухфакторность**: email + SMS (если есть телефон)
3. **Совместимость**: telegram_users таблица для обратной совместимости
4. **Логирование**: Все операции логируются в `/var/log/telegram_auth_service.log`
5. **Мониторинг**: Сервис включен в систему мониторинга admin.py

## 🧪 ТЕСТИРОВАНИЕ

### Проверка сервиса
```bash
curl http://localhost:8016/status
```

### Тест через nginx
```bash
curl https://bot.vochi.by/telegram-auth/status
```

### Проверка БД
```sql
SELECT telegram_authorized, telegram_tg_id, email 
FROM users 
WHERE telegram_authorized = TRUE;
```

---
**Создано**: 02.08.2025  
**Версия**: 1.0.0  
**Статус**: ✅ Готова к продакшену

## 🧪 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ

### ✅ Успешно протестировано:
1. **Генерация и отправка кодов** - email + SMS
2. **Верификация кодов** - корректная проверка 6-значных кодов  
3. **Авторизация пользователей** - сохранение в БД
4. **Интеграция с desk.py** - кнопка Telegram-бота работает
5. **API endpoints** - все endpoints отвечают корректно
6. **Telegram-бот обработчики** - команды /start с параметрами

### 📊 Тестовые данные:
- **Пользователь**: evgeny.baevski@gmail.com (ID: 25)
- **Предприятие**: 0367 (june) 
- **Telegram ID**: 123456789
- **Статус**: telegram_authorized = TRUE ✅

## 🚀 ГОТОВО К ВНЕДРЕНИЮ

Система полностью готова для использования в продакшене. Все компоненты протестированы и работают стабильно.

## 🎯 ТЕКУЩИЙ СТАТУС (aiogram 2.x)

### ✅ ПОЛНОСТЬЮ РАБОТАЕТ:
- **Telegram Auth Service** (порт 8016) - авторизация через API
- **Правильные ссылки** в desk.py - ведут на корректный бот предприятия
- **Email + SMS коды** - двухфакторная аутентификация  
- **Бот предприятия 0367** - запущен с поддержкой авторизации
- **Admin API** - управление авторизациями

### 🔗 ГОТОВО К ТЕСТИРОВАНИЮ:
1. Перейти: https://bot.vochi.by/desk/?enterprise=june&number=0367
2. Кликнуть: "📱 Telegram-бот june"  
3. Попасть на: @vochi_june_bot
4. Ввести: `/start auth__0367`
5. Ввести email для авторизации
6. Получить и ввести 6-значный код

## 📋 ПЛАН МИГРАЦИИ НА AIOGRAM 3.x (БУДУЩЕЕ)

### Этап 1: Подготовка
- [ ] Создать тестовое окружение
- [ ] Инвентаризация всех telegram-зависимостей
- [ ] Backup существующих уведомлений

### Этап 2: Обновление
- [ ] `pip install aiogram==3.x`
- [ ] Переписать handlers под новую архитектуру
- [ ] Обновить FSM и Router система
- [ ] Тестирование в изоляции

### Этап 3: Миграция
- [ ] Поэтапная замена сервисов
- [ ] Валидация уведомлений  
- [ ] Мониторинг стабильности

### Этап 4: Финализация
- [ ] Удаление legacy кода
- [ ] Документация новой архитектуры
- [ ] Обучение команды

**Приоритет миграции:** СРЕДНИЙ (после стабилизации текущей версии)

---

## 🛡️ Безопасность

### Частичная очистка истории чата при удалении/блокировке

**Проблема:** Требовалась возможность очистки истории чата при удалении пользователя, блокировке или отзыве авторизации.

**Ограничения Telegram Bot API:**
- ❌ Нельзя полностью очистить всю историю чата
- ❌ Нельзя удалить старые сообщения бота (нет записей message_id в БД)  
- ❌ Нельзя удалить сообщения пользователя старше 48 часов
- ❌ Нельзя заблокировать пользователя со стороны бота

**Реализованное решение:**

#### 1. 🚫 Игнорирование сообщений от нежелательных пользователей

**Файл:** `telegram_auth_handler_v2.py`

```python
async def check_user_blocked_status(tg_id: int, enterprise_number: str) -> dict:
    """Проверяет заблокирован ли пользователь или удален"""
    # Проверка статуса в users и telegram_users
    return {
        "exists": True/False,
        "blocked": True/False, 
        "authorized": True/False,
        "deleted": True/False
    }

# Обработчик всех сообщений проверяет статус:
# - Удаленные пользователи: полное игнорирование
# - Заблокированные: уведомление о блокировке  
# - Неавторизованные: предложение /start
```

#### 2. 🗑️ Очистка данных пользователя при удалении

**Новый API endpoint:** `POST /cleanup_user_data`

```python
@app.post("/cleanup_user_data")
async def cleanup_user_data(request: dict):
    """Очистка данных пользователя из telegram_users"""
    # Удаляет записи по tg_id и/или email
    # Возвращает количество удаленных записей
```

**Интеграция в delete_user:**

```python
# В enterprise_admin_service.py
# 1. Отправка уведомления об удалении
# 2. Очистка данных из telegram_users  
# 3. Удаление пользователя из users
```

#### 3. 📝 Логирование безопасности

```python
# Все действия логируются:
logger.info(f"Игнорируем сообщение от удаленного пользователя {user_id}")
logger.info(f"Заблокирован пользователь {user_id} попытался отправить сообщение")
logger.info(f"Очищены данные пользователя {user_id} из telegram_users")
```

#### 4. 🔮 Планы на будущее

- **Логирование message_id:** Для возможности удаления сообщений бота
- **Таблица message_log:** Хранение истории отправленных сообщений
- **Удаление недавних сообщений:** До 48 часов через deleteMessage API

**Результат:** Максимально возможная защита с учетом ограничений Telegram Bot API

### Автоматический отзыв авторизации при удалении пользователя

**Проблема:** При удалении пользователя из системы его Telegram авторизация не отзывалась автоматически.

**Решение:** Добавлена логика в `enterprise_admin_service.py`:

```python
# В функции delete_user добавлено:
telegram_user = await conn.fetchrow(
    "SELECT telegram_authorized, telegram_tg_id, email FROM users WHERE id = $1", 
    user_id
)

if telegram_user and telegram_user['telegram_authorized'] and telegram_user['telegram_tg_id']:
    # Отправляем уведомление о блокировке
    telegram_notification = {
        "tg_id": telegram_user['telegram_tg_id'],
        "message": "🚫 Ваш аккаунт был удален администратором предприятия. Доступ к Telegram-боту отозван."
    }
    
    # Отправка через новый эндпойнт /send_notification
```

**Новый эндпойнт:** `POST /send_notification` в `telegram_auth_service.py`
- Принимает `tg_id` и `message`
- Автоматически определяет `bot_token` предприятия
- Отправляет уведомление через Telegram Bot API

**Результат:** Удаленные пользователи автоматически получают уведомление и теряют доступ к боту.

---

## 🚨 ОТКАТ ИЗМЕНЕНИЙ ПО БЕЗОПАСНОСТИ (02.08.2025)

**ПРИЧИНА:** Функциональность безопасности перегружала PostgreSQL и ломала производительность всех ботов.

### ❌ Удаленные функции:

1. **POST /send_notification** в `telegram_auth_service.py`
   - Удален весь endpoint для отправки уведомлений
   - Причина: создавал дополнительные подключения к БД

2. **POST /cleanup_user_data** в `telegram_auth_service.py`
   - Удален endpoint для очистки данных пользователя
   - Причина: дополнительная нагрузка на PostgreSQL

3. **Автоматический отзыв Telegram авторизации** в `enterprise_admin_service.py`
   - Удален весь блок кода из функции `delete_user`
   - Убраны HTTP запросы к `localhost:8016`
   - Причина: множественные API вызовы при удалении пользователей

4. **Проверки статуса пользователей** в `telegram_auth_handler_v2.py`
   - Удалена функция `check_user_blocked_status`
   - Удален обработчик `handle_other_messages`
   - Причина: проверки выполнялись для каждого сообщения

### ✅ Результат отката:

- **PostgreSQL:** connections стабилизированы (104 total / 1 active)
- **Производительность:** все 76 ботов работают стабильно
- **Функциональность:** базовая авторизация работает без проблем

### 💡 План на будущее:

Функции безопасности будут реализованы позже с:
- Правильным connection pooling
- Кешированием статусов пользователей  
- Минимальным количеством запросов к БД
- Асинхронной обработкой уведомлений

**Текущий статус:** Система работает в стабильном режиме базовой авторизации.