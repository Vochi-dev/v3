# 🔧 Руководство по отладке системы событий Asterisk

## 📍 Расположение файлов логов

### **Сервис 8025 (Эмулятор событий)**
- **Основной лог событий:** `/root/asterisk-webhook/logs/call_tester_events.log`
- **Описание:** Детальное логирование всех эмулированных событий
- **Содержимое:**
  - Все отправленные события (dial, bridge_create, bridge, bridge_leave, bridge_destroy, hangup, new_callerid)
  - Полные JSON данные каждого события
  - HTTP заголовки запросов и ответов
  - Статусы ответов от сервера bot.vochi.by
  - Серверные ошибки и их детали
  - Временные метки отправки и получения

**Команды для работы:**
```bash
# Просмотр всех логов эмулятора
cat logs/call_tester_events.log

# Последние события
tail -n 100 logs/call_tester_events.log

# Мониторинг в реальном времени
tail -f logs/call_tester_events.log

# Очистка лога перед тестированием
cat /dev/null > logs/call_tester_events.log

# Поиск конкретного события
grep -A 10 "🚀 EMULATION" logs/call_tester_events.log

# Поиск ошибок
grep "ERROR" logs/call_tester_events.log
```

---

### **Сервис 8000 (Основной webhook-сервер) - Предприятие 0367/june**
- **Специальный лог для 0367:** `/root/asterisk-webhook/logs/0367.log`
- **Token:** `375293332255`
- **Описание:** Изолированное логирование событий только от тестового предприятия 0367 (june)
- **Содержимое:**
  - Тип события (start, dial, bridge, hangup и т.д.)
  - Token и UniqueId
  - Полное тело события в JSON формате
  - Эмодзи-маркеры для быстрого визуального поиска

**Команды для работы:**
```bash
# Просмотр всех событий 0367
cat logs/0367.log

# Последние события
tail -n 50 logs/0367.log

# Мониторинг в реальном времени
tail -f logs/0367.log

# Очистка лога перед тестированием
cat /dev/null > logs/0367.log

# Поиск по типу события
grep "hangup" logs/0367.log
grep "bridge" logs/0367.log

# Поиск по UniqueId
grep "1759225684.16" logs/0367.log

# Подсчет событий каждого типа
grep "🧪 TEST EVENT:" logs/0367.log | awk '{print $NF}' | sort | uniq -c
```

---

### **Сервис 8000 (Основной webhook-сервер) - Все предприятия**
- **Основной лог:** `/root/asterisk-webhook/logs/app.log`
- **Uvicorn лог:** `/root/asterisk-webhook/logs/uvicorn.log`
- **Лог доступа:** `/root/asterisk-webhook/logs/access.log`
- **Описание:** Общие логи от всех предприятий (сотни хостов)
- **Формат:** `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- **Ротация:** 10MB на файл, 5 бэкапов

**Команды для работы:**
```bash
# Просмотр основного лога
tail -f logs/app.log

# Фильтрация по Token 0367
grep "375293332255" logs/app.log

# Поиск ошибок
grep "ERROR" logs/app.log
```

---

## 🧪 Типичный процесс отладки

### **Шаг 1: Подготовка**
```bash
# Очищаем логи эмулятора и 0367
cat /dev/null > logs/call_tester_events.log
cat /dev/null > logs/0367.log
```

### **Шаг 2: Эмуляция события**
- Открыть интерфейс: https://bot.vochi.by/test-interface
- Выбрать нужный паттерн
- Заполнить форму
- Нажать "Отправить события"

### **Шаг 3: Проверка отправки (сервис 8025)**
```bash
# Проверяем что события отправились
tail -50 logs/call_tester_events.log | grep "📥 EMULATION RESPONSE"
```

### **Шаг 4: Проверка получения (сервис 8000)**
```bash
# Проверяем что события получены на основном сервере
tail -50 logs/0367.log
```

### **Шаг 5: Анализ**
- Сравнить отправленные и полученные события
- Проверить корректность данных
- Выявить расхождения

---

## 📊 Структура логов 0367.log

**Пример записи:**
```
2025-10-01 14:23:45,123 [INFO] 🧪 TEST EVENT: hangup
2025-10-01 14:23:45,124 [INFO] 📋 Token: 375293332255, UniqueId: 1759230684.16
2025-10-01 14:23:45,125 [INFO] 📦 Full Body: {
  "Token": "375293332255",
  "CallStatus": "2",
  "Phone": "152",
  "ExternalInitiated": true,
  "Trunk": "",
  "Extensions": ["150"],
  "UniqueId": "1759230684.16",
  "StartTime": "",
  "DateReceived": "2025-09-30 11:15:02",
  "EndTime": "2025-09-30 11:15:12",
  "CallType": 0
}
```

---

## 🔄 Перезапуск сервисов

**❌ НЕ ИСПОЛЬЗОВАТЬ:**
```bash
./all.sh restart  # Может привести к проблемам с запуском отдельных сервисов
```

**✅ ПРАВИЛЬНО:**
```bash
# Перезапуск только основного сервиса 8000
./main.sh restart

# Перезапуск эмулятора 8025
./call_tester.sh restart
```

**⚠️ ВАЖНО:** Если требуется перезапуск всех сервисов - сообщить пользователю, он выполнит `./all.sh restart` вручную.

---

## 🎯 Быстрые команды для работы

```bash
# Одновременный мониторинг эмулятора и основного сервера
tail -f logs/call_tester_events.log logs/0367.log

# Очистка обоих логов
cat /dev/null > logs/call_tester_events.log && cat /dev/null > logs/0367.log && echo "✅ Логи очищены"

# Подсчет событий в текущей сессии
echo "События от эмулятора:" && grep -c "📥 EMULATION RESPONSE" logs/call_tester_events.log
echo "События на сервере 0367:" && grep -c "🧪 TEST EVENT" logs/0367.log

# Быстрая проверка последнего события
echo "=== Последнее отправленное ===" && grep "📊 Event Data:" logs/call_tester_events.log | tail -1
echo "=== Последнее полученное ===" && grep "🧪 TEST EVENT" logs/0367.log | tail -1
```

---

## 📝 История изменений

### 2025-10-01 - Создание системы отладки
- ✅ Создан отдельный лог для предприятия 0367 (`logs/0367.log`)
- ✅ Настроено параллельное логирование событий от Token `375293332255`
- ✅ Добавлены эмодзи-маркеры для визуального поиска
- ✅ Создан файл `otladka.md` с руководством по отладке


