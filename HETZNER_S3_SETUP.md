# 🚀 Настройка Hetzner Object Storage для записей разговоров

## 📋 Обзор

Этот проект теперь поддерживает централизованное хранение записей телефонных разговоров в **Hetzner Object Storage** (S3-совместимое облачное хранилище).

### ✅ Преимущества решения:
- **100% S3-совместимость** - работает со всеми стандартными S3 библиотеками
- **Автоматическое масштабирование** - до 100TB на bucket
- **Временные ссылки** - безопасная отправка записей в CRM системы
- **Программный доступ** - полная интеграция через API
- **Lifecycle policies** - автоматическое удаление старых записей

---

## 🔧 Быстрая настройка

### Шаг 1: Получение S3 credentials

1. Откройте **Hetzner Console**: https://console.hetzner.com/
2. Перейдите в ваш проект
3. Выберите **Security** → **S3 Credentials**
4. Нажмите **"Generate credentials"**
5. **Сохраните ключи** (Secret Key показывается только один раз!)

### Шаг 2: Настройка конфигурации

```bash
# Скопируйте пример конфигурации
cp s3_config.example.py s3_config.py

# Отредактируйте файл своими ключами
nano s3_config.py
```

**Замените в файле `s3_config.py`:**
```python
'ACCESS_KEY': 'ВАШ_РЕАЛЬНЫЙ_ACCESS_KEY',
'SECRET_KEY': 'ВАШ_РЕАЛЬНЫЙ_SECRET_KEY',
```

### Шаг 3: Установка зависимостей

```bash
# Установка AWS SDK
pip install -r requirements.txt

# Или отдельно boto3
pip install boto3==1.34.144
```

### Шаг 4: Тестирование подключения

```bash
# Запуск полного теста
python test_s3_connection.py
```

**Ожидаемый результат:**
```
🔧 Тестирование подключения к Hetzner Object Storage...
✅ Конфигурация корректна
✅ S3 клиент создан для региона fsn1
✅ Bucket 'vochi' готов к использованию
✅ Файл загружен: https://vochi.fsn1.your-objectstorage.com/...
✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!
```

---

## 📊 Информация о вашем хранилище

| Параметр | Значение |
|----------|----------|
| **Endpoint** | `fsn1.your-objectstorage.com` |
| **Bucket** | `vochi` |
| **Регион** | `fsn1` (Falkenstein) |
| **URL Bucket** | `https://vochi.fsn1.your-objectstorage.com/` |

### 🏗️ Структура файлов в хранилище:
```
vochi/
└── call-recordings/
    └── 2025/
        └── 07/
            └── 23/
                ├── 0387/
                │   ├── call_1721724567.wav
                │   └── call_1721724890.wav
                └── 0275/
                    └── call_1721725123.wav
```

---

## 🔌 Интеграция в проект

### Базовое использование:

```python
from hetzner_s3_integration import HetznerS3Client
from s3_config import S3_CONFIG

# Создание клиента
s3_client = HetznerS3Client(
    access_key=S3_CONFIG['ACCESS_KEY'],
    secret_key=S3_CONFIG['SECRET_KEY']
)

# Загрузка записи разговора
file_url = s3_client.upload_call_recording(
    enterprise_number="0387",
    call_unique_id="call_unique_123456",
    local_file_path="/path/to/recording.wav"
)

print(f"Запись загружена: {file_url}")
```

### Поиск записей:

```python
from datetime import datetime, timedelta

# Поиск записей за последнюю неделю
recordings = s3_client.find_recordings(
    enterprise_number="0387",
    date_from=datetime.now() - timedelta(days=7),
    date_to=datetime.now()
)

for recording in recordings:
    print(f"Файл: {recording['key']}")
    print(f"Размер: {recording['size']} байт")
    print(f"URL: {recording['download_url']}")
```

### Временные ссылки для CRM:

```python
# Создание ссылки на 24 часа
download_link = s3_client.generate_download_link(
    object_key="call-recordings/2025/07/23/0387/call_123456.wav",
    expires_in=86400  # 24 часа
)

# Отправляем эту ссылку в CRM систему
send_to_crm(download_link)
```

---

## 💰 Тарификация и лимиты

### 📊 Стоимость:
```
Базовая плата: €4.99/месяц
├── Включает: 1TB хранения
├── Включает: 1TB исходящего трафика  
├── Дополнительно: €4.99/TB хранения
└── Дополнительно: €1.00/TB трафика
```

### 📏 Технические лимиты:
- **Максимум файлов:** 50,000,000 на bucket
- **Максимум размер файла:** 5TB
- **Максимум размер bucket:** 100TB
- **Скорость:** до 750 запросов/сек на bucket
- **Пропускная способность:** до 10 Гбит/сек на bucket

---

## 🛡️ Безопасность

### 🔐 Конфигурация:
- ✅ Файл `s3_config.py` добавлен в `.gitignore` 
- ✅ Поддержка переменных окружения
- ✅ Временные подписанные ссылки
- ✅ Приватный bucket по умолчанию

### 🌍 Переменные окружения (рекомендуется для продакшена):
```bash
export HETZNER_S3_ACCESS_KEY="ваш_access_key"
export HETZNER_S3_SECRET_KEY="ваш_secret_key"
```

---

## 🔄 Автоматизация

### Lifecycle Policies (автоматическое удаление старых записей):

```python
# Удаление записей старше 90 дней
deleted_count = s3_client.delete_old_recordings(days_to_keep=90)
print(f"Удалено старых записей: {deleted_count}")
```

### Cron задача для очистки:
```bash
# Добавить в crontab -e
0 3 * * * cd /root/asterisk-webhook && python -c "from hetzner_s3_integration import HetznerS3Client; from s3_config import S3_CONFIG; s3=HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY']); s3.delete_old_recordings(90)"
```

---

## 📋 Примеры использования

### 1. Централизованная выгрузка записей с Asterisk серверов:

```python
def daily_backup_recordings():
    """Ежедневная выгрузка записей в Object Storage"""
    
    # Получаем список файлов за сегодня
    today_recordings = get_today_recordings_from_asterisk()
    
    for recording in today_recordings:
        file_url = s3_client.upload_call_recording(
            enterprise_number=recording['enterprise'],
            call_unique_id=recording['unique_id'],
            local_file_path=recording['file_path'],
            call_date=recording['call_date']
        )
        
        if file_url:
            # Обновляем БД с URL в облаке
            update_call_record_url(recording['id'], file_url)
            
            # Удаляем локальный файл
            os.remove(recording['file_path'])
```

### 2. API для получения записей:

```python
@app.get("/api/recordings/{enterprise_number}")
async def get_recordings(enterprise_number: str, date_from: str, date_to: str):
    """API endpoint для получения списка записей"""
    
    recordings = s3_client.find_recordings(
        enterprise_number=enterprise_number,
        date_from=datetime.fromisoformat(date_from),
        date_to=datetime.fromisoformat(date_to)
    )
    
    # Генерируем временные ссылки для каждой записи
    for recording in recordings:
        recording['download_link'] = s3_client.generate_download_link(
            recording['key'], 
            expires_in=3600  # 1 час
        )
    
    return {"recordings": recordings}
```

### 3. Интеграция с CRM системами:

```python
def send_recording_to_crm(call_id: str, crm_webhook_url: str):
    """Отправка ссылки на запись в CRM систему"""
    
    # Находим запись по ID
    recording = find_recording_by_call_id(call_id)
    
    # Генерируем временную ссылку на 7 дней
    download_url = s3_client.generate_download_link(
        recording['key'], 
        expires_in=604800  # 7 дней
    )
    
    # Отправляем в CRM
    payload = {
        "call_id": call_id,
        "recording_url": download_url,
        "expires_at": (datetime.now() + timedelta(days=7)).isoformat()
    }
    
    response = requests.post(crm_webhook_url, json=payload)
    return response.status_code == 200
```

---

## 🆘 Решение проблем

### ❌ Ошибка: "ACCESS_KEY не настроен"
**Решение:** Отредактируйте `s3_config.py` и укажите реальные ключи

### ❌ Ошибка: "NoSuchBucket"
**Решение:** Bucket будет создан автоматически при первом запуске

### ❌ Ошибка: "SignatureDoesNotMatch" 
**Решение:** Проверьте правильность SECRET_KEY

### ❌ Медленная загрузка файлов
**Решение:** Используйте multipart upload для файлов > 5MB:
```python
# Для больших файлов используется автоматически
s3_client.s3_client.upload_file(
    large_file_path, bucket, key,
    Config=boto3.s3.transfer.TransferConfig(
        multipart_threshold=1024 * 25,  # 25MB
        max_concurrency=10,
        multipart_chunksize=1024 * 25,
        use_threads=True
    )
)
```

---

## 📞 Поддержка

**Документация Hetzner:** https://docs.hetzner.com/storage/object-storage/  
**Boto3 документация:** https://boto3.amazonaws.com/v1/documentation/api/latest/

### 📈 Мониторинг использования:

```python
# Получение статистики хранилища
usage = s3_client.get_storage_usage()
print(f"Файлов: {usage['total_files']}")
print(f"Размер: {usage['total_size_gb']} GB")

# Проверка остатка включенной квоты
# (требует дополнительной реализации через Hetzner Cloud API)
```

---

## ✅ Готово к использованию!

После выполнения всех шагов у вас будет:

🎯 **Централизованное хранилище** записей разговоров  
🔗 **API для интеграций** с CRM системами  
🔄 **Автоматическое управление** жизненным циклом файлов  
💰 **Оптимизированные затраты** на хранение  
🛡️ **Безопасный доступ** через временные ссылки 