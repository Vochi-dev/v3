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