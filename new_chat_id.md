# 📋 Инструкция: Добавление предприятия в telegram_users

## Описание проблемы
По умолчанию все сообщения от ботов отправляются только суперюзеру с chat_id `374573193`. Чтобы сообщения приходили на новый chat_id (например, `989104050`), нужно добавить запись в таблицу `telegram_users`.

**✅ ВАЖНО:** С июля 2025 года один chat_id может подписываться на несколько ботов (изменена структура БД)!

## Шаг 1: Узнать bot_token предприятия
```sql
-- Замени 'XXXX' на номер предприятия (например, '0116')
SELECT number, bot_token FROM enterprises WHERE number = 'XXXX';
```

## Шаг 2: Проверить текущих подписчиков (опционально)
```sql
-- Замени 'BOT_TOKEN_ИЗ_ШАГА_1' на полученный токен
SELECT tg_id, bot_token FROM telegram_users WHERE bot_token = 'BOT_TOKEN_ИЗ_ШАГА_1';
```

## Шаг 3: Добавить нового пользователя
```sql
-- Замени:
-- XXXXXXXXX - на chat_id (например, 989104050)  
-- BOT_TOKEN_ИЗ_ШАГА_1 - на bot_token из шага 1
-- support@example.com - на любой уникальный email (можно оставить как есть)

INSERT INTO telegram_users (tg_id, email, bot_token) VALUES 
(XXXXXXXXX, 'support@example.com', 'BOT_TOKEN_ИЗ_ШАГА_1');
```

## Шаг 4: Проверить результат
```sql
-- Замени 'BOT_TOKEN_ИЗ_ШАГА_1' на токен из шага 1
SELECT tg_id, bot_token FROM telegram_users WHERE bot_token = 'BOT_TOKEN_ИЗ_ШАГА_1';
```

---

## 🔧 Команды для PostgreSQL через терминал

### Подключение к БД:
```bash
PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres
```

### Все команды в одном блоке (замени значения):
```sql
-- 1. Узнать bot_token
SELECT number, bot_token FROM enterprises WHERE number = 'XXXX';

-- 2. Добавить пользователя (замени XXXXXXXXX и BOT_TOKEN)
INSERT INTO telegram_users (tg_id, email, bot_token) VALUES 
(XXXXXXXXX, 'support@example.com', 'BOT_TOKEN_ИЗ_ШАГА_1');

-- 3. Проверить результат
SELECT tg_id, bot_token FROM telegram_users WHERE bot_token = 'BOT_TOKEN_ИЗ_ШАГА_1';

-- 4. Выйти
\q
```

---

## 📝 Пример для предприятия 0123 с chat_id 555666777

```sql
-- 1. Узнать токен
SELECT number, bot_token FROM enterprises WHERE number = '0123';
-- Допустим получили: 7123456789:AAEexampleTokenHere

-- 2. Добавить пользователя  
INSERT INTO telegram_users (tg_id, email, bot_token) VALUES 
(555666777, 'support@example.com', '7123456789:AAEexampleTokenHere');

-- 3. Проверить
SELECT tg_id, bot_token FROM telegram_users WHERE bot_token = '7123456789:AAEexampleTokenHere';
```

---

## ⚠️ Важные моменты

1. **tg_id** должен быть числом (без кавычек)
2. **email** должен быть уникальным в БД для каждой пары (tg_id, bot_token)
3. **bot_token** должен точно совпадать с токеном из enterprises
4. **Один chat_id может получать сообщения от НЕСКОЛЬКИХ ботов** (с июля 2025)
5. После добавления сообщения будут приходить И в новый chat_id, И в суперюзера 374573193

---

## 📋 Текущие chat_id группы

- **374573193** → старые боты (созданные до лимита 40)
- **989104050** → новые боты (0116, 0110, 0114, и другие до лимита 40)
- **Следующий chat_id** → будет создан при превышении лимита 40 ботов

---

## 🧪 Тест после добавления

```bash
# Замени TOKEN_ASTERISK на name2 из enterprises, а остальные параметры по желанию
curl -X POST "https://bot.vochi.by/dial" -H "Content-Type: application/json" -d '{"Token": "TOKEN_ASTERISK", "CallType": 1, "UniqueId": "test_dial_XXXX", "ExtPhone": "", "Trunk": "0001363", "Extensions": ["151"], "Phone": "375291234567", "ExtTrunk": ""}'
```

### В логах должно появиться:
```
Found bot_token: YOUR_BOT_TOKEN, tg_ids: [XXXXXXXXX, 374573193]
Successfully sent to chat_id: XXXXXXXXX
```

---

## 🔍 Диагностика проблем

### Найти предприятия без подписчиков:
```sql
SELECT e.number, e.bot_token, COUNT(tu.tg_id) as user_count 
FROM enterprises e 
LEFT JOIN telegram_users tu ON e.bot_token = tu.bot_token 
WHERE e.bot_token IS NOT NULL AND e.bot_token != '' 
GROUP BY e.number, e.bot_token 
HAVING COUNT(tu.tg_id) = 0
ORDER BY CAST(e.number AS INTEGER);
```

### Посмотреть все подписки по предприятию:
```sql
SELECT e.number, e.name, tu.tg_id, tu.email 
FROM enterprises e 
JOIN telegram_users tu ON e.bot_token = tu.bot_token 
WHERE e.number = 'XXXX';
```

### Посмотреть все боты для конкретного chat_id:
```sql
SELECT e.number, e.name, tu.tg_id, tu.bot_token
FROM telegram_users tu
JOIN enterprises e ON tu.bot_token = e.bot_token
WHERE tu.tg_id = XXXXXXXXX
ORDER BY CAST(e.number AS INTEGER);
```

---

## 📊 Схема работы системы

1. **Asterisk отправляет событие** с токеном (например, `375445561561`)
2. **Система ищет предприятие** по `enterprises.name2 = токен`
3. **Получает bot_token** из найденного предприятия
4. **Ищет подписчиков** в `telegram_users.bot_token = bot_token`
5. **Добавляет суперюзера** 374573193 если его нет в списке
6. **Отправляет сообщения** всем найденным chat_id

**Без записи в telegram_users** → сообщения идут только в 374573193  
**С записью в telegram_users** → сообщения идут в новый chat_id + 374573193

**Новая возможность:** Один chat_id может быть подписан на несколько ботов одновременно! 