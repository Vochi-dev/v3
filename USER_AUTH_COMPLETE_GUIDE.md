# 🔐 **Полное руководство системы авторизации пользователей Vochi CRM**

## 📋 **Обзор системы**

Система авторизации пользователей Vochi CRM обеспечивает безопасный доступ к платформе через email + SMS верификацию с последующим ролевым доступом к различным сервисам.

## 🎯 **Пользовательский сценарий**

1. **Пользователь** заходит на `bot.vochi.by`
2. **Автоматическое перенаправление** на сервис авторизации
3. **Ввод email** → система отправляет 6-значный код на email + SMS
4. **Ввод кода** → создание сессии, сохранение в cookie
5. **Попадание на Рабочий стол** с кнопками в зависимости от ролей
6. **Доступ к функциям** согласно правам пользователя

## 🏗️ **Архитектура системы**

### **Компоненты:**
- **Main Service** (порт 8000) - основной FastAPI сервис с AuthMiddleware
- **Auth Service** (порт 8015) - микросервис авторизации
- **Desk Service** (порт 8011) - "Рабочий стол" с ролевыми кнопками
- **Admin Service** (порт 8004) - административная панель суперадминистратора
- **SMS Services** (порты 8013, 8014) - отправка SMS кодов

### **База данных:**
- **`users`** - пользователи с ролями
- **`auth_codes`** - временные коды авторизации
- **`user_sessions`** - активные сессии пользователей

## 🔐 **Настройка и развертывание**

### **1. Запуск сервисов**
```bash
# Запуск всех сервисов
./all.sh restart

# Проверка статуса
./all.sh status

# Запуск отдельных компонентов
./auth.sh start
./desk.sh start
./admin.sh start
```

### **2. Конфигурация базы данных**

#### **Создание таблиц:**
```sql
-- Расширение таблицы users ролями
ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN is_employee BOOLEAN DEFAULT TRUE;
ALTER TABLE users ADD COLUMN is_marketer BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN is_spec1 BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN is_spec2 BOOLEAN DEFAULT FALSE;

-- Таблица кодов авторизации
CREATE TABLE auth_codes (
    email VARCHAR(255) PRIMARY KEY,
    code VARCHAR(6) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT NOW() + INTERVAL '10 minutes'
);

-- Таблица сессий пользователей
CREATE TABLE user_sessions (
    session_token VARCHAR(255) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    enterprise_number VARCHAR(10) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT NOW() + INTERVAL '24 hours'
);

CREATE INDEX idx_user_sessions_expires ON user_sessions(expires_at);
```

### **3. Параметры сервисов**

#### **Auth Service (порт 8015):**
```python
CODE_LENGTH = 6                    # Длина кода авторизации
CODE_EXPIRY_MINUTES = 10          # Время жизни кода (минуты)
SESSION_EXPIRY_HOURS = 24         # Время жизни сессии (часы)
```

#### **AuthMiddleware в Main Service:**
```python
PUBLIC_ROUTES = {
    "/", "/admin", "/admin/login", "/admin/dashboard", "/admin/enterprises",
    "/health", "/docs", "/redoc", "/openapi.json",
    "/start", "/dial", "/bridge", "/hangup",  # Asterisk webhooks
    "/bridge_create", "/bridge_leave", "/bridge_destroy", "/new_callerid"
}
```

## 👥 **Управление пользователями и ролями**

### **Роли пользователей:**
- **🟢 Администратор** (`is_admin=true`):
  - Полный доступ к админ-панели предприятия
  - Кнопка "👤 Панель администратора" в Рабочем столе
  - При выборе блокирует все остальные роли

- **🟡 Маркетолог** (`is_marketer=true`):
  - Доступ к статистике и аналитике
  - Кнопка "📊 Статистика" в Рабочем столе

- **⚪ Сотрудник** (`is_employee=true`):
  - Базовый доступ к Рабочему столу
  - Роль по умолчанию

- **🔧 Spec1/Spec2** (`is_spec1`, `is_spec2`):
  - Специализированные роли для будущего функционала

### **Создание пользователей:**
1. Зайти в админ-панель предприятия: `bot.vochi.by/admin/login`
2. Перейти к управлению пользователями
3. Нажать "Создать пользователя"
4. Заполнить данные и выбрать роли
5. Сохранить изменения

### **Логика ролей:**
- **Администратор** → отключает все остальные роли
- **Остальные роли** → могут комбинироваться
- **По умолчанию** → роль "Сотрудник"

## 🌐 **API Endpoints**

### **Auth Service (localhost:8015):**
```
GET  /                    - Стартовая страница ввода email
POST /send-code          - Отправка кода авторизации
GET  /verify             - Страница ввода кода
POST /verify-code        - Проверка кода и создание сессии
GET  /logout             - Выход из системы
GET  /health             - Проверка состояния сервиса
```

### **Main Service (localhost:8000):**
```
GET  /                    - Перенаправление на auth сервис
GET  /admin/*            - Публичные админ маршруты
POST /start, /dial, etc. - Asterisk webhooks (публичные)
```

### **Desk Service (localhost:8011):**
```
GET  /?enterprise=<name>&number=<num> - Рабочий стол с ролевыми кнопками
GET  /health             - Проверка состояния
```

## 🔧 **Администрирование**

### **Мониторинг сервисов:**
1. Зайти в админ-панель: `bot.vochi.by/admin/login`
2. Нажать кнопку "Service" в навигации
3. Просмотр статуса всех сервисов
4. Управление (stop/restart) сервисами

### **Логи и отладка:**
```bash
# Логи auth сервиса
tail -f /var/log/auth_service.log

# Логи main сервиса  
tail -f /var/log/main_service.log

# Логи desk сервиса
tail -f /var/log/desk_service.log

# Проверка процессов
ps aux | grep uvicorn

# Проверка портов
netstat -tlnp | grep -E '8000|8004|8011|8015'
```

### **Очистка данных:**
```sql
-- Удаление истекших кодов
DELETE FROM auth_codes WHERE expires_at < NOW();

-- Удаление истекших сессий
DELETE FROM user_sessions WHERE expires_at < NOW();

-- Сброс всех сессий пользователя
DELETE FROM user_sessions WHERE user_id = <user_id>;
```

## 🛡️ **Безопасность**

### **Защита маршрутов:**
- **AuthMiddleware** автоматически проверяет все запросы
- **PUBLIC_ROUTES** список исключений
- **Session validation** проверка актуальности токенов
- **Automatic cleanup** удаление истекших данных

### **Рекомендации:**
- Регулярно обновляйте пароли админов
- Мониторьте активные сессии пользователей
- Проверяйте логи на подозрительную активность
- Используйте HTTPS для всех соединений

## 🧪 **Тестирование**

### **Полный flow тестирования:**
```bash
# 1. Проверка перенаправления
curl -I http://localhost:8000/

# 2. Проверка auth сервиса
curl http://localhost:8015/health

# 3. Отправка кода
curl -X POST http://localhost:8015/send-code \
  -d "email=test@example.com"

# 4. Проверка кода в БД
psql -h localhost -U postgres -d postgres \
  -c "SELECT * FROM auth_codes WHERE email='test@example.com';"

# 5. Проверка сессии
curl --cookie "session_token=..." http://localhost:8011/
```

### **Проверка ролей:**
1. Создать пользователей с разными ролями
2. Авторизоваться под каждым
3. Проверить отображение кнопок в Рабочем столе
4. Убедиться в правильности доступов

## 🚨 **Устранение неполадок**

### **Частые проблемы:**

**1. Сервис не запускается:**
```bash
# Проверить порт
sudo lsof -i :8015

# Проверить логи
tail -f /var/log/auth_service.log

# Перезапустить
./auth.sh restart
```

**2. Коды не отправляются:**
- Проверить SMS сервис (порт 8013)
- Проверить email интеграцию
- Проверить логи auth сервиса

**3. Сессии не работают:**
- Проверить БД подключение
- Проверить время истечения
- Очистить старые сессии

**4. Роли не отображаются:**
- Проверить данные пользователя в БД
- Проверить логи desk сервиса
- Перезапустить auth и desk сервисы

## 📊 **Статистика и мониторинг**

### **Полезные запросы:**
```sql
-- Активные сессии
SELECT COUNT(*) FROM user_sessions WHERE expires_at > NOW();

-- Статистика по ролям
SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN is_admin THEN 1 ELSE 0 END) as admins,
    SUM(CASE WHEN is_marketer THEN 1 ELSE 0 END) as marketers,
    SUM(CASE WHEN is_employee THEN 1 ELSE 0 END) as employees
FROM users WHERE status = 'active';

-- Последние авторизации
SELECT u.email, s.created_at, s.enterprise_number
FROM user_sessions s
JOIN users u ON s.user_id = u.id
ORDER BY s.created_at DESC LIMIT 10;
```

## 📝 **Changelog**

### **v1.0.0 (Август 2025)**
- ✅ Создана система авторизации через email + SMS
- ✅ Реализованы роли пользователей
- ✅ Добавлен AuthMiddleware для защиты маршрутов
- ✅ Интегрированы ролевые кнопки в Рабочий стол
- ✅ Создан мониторинг сервисов в админ-панели
- ✅ Настроена автоматическая очистка истекших данных

## 🔗 **Ссылки**

- **Главная**: `https://bot.vochi.by/`
- **Админ-панель**: `https://bot.vochi.by/admin/login`
- **Auth сервис**: `http://localhost:8015/`
- **Рабочий стол**: `http://localhost:8011/`
- **Документация API**: `http://localhost:8000/docs`

---

**💡 Для дополнительной поддержки обращайтесь к администратору системы.** 