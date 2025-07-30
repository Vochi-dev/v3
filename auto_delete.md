# 🗑️ Автоматическое удаление файлов из S3

## 📋 Общая информация

Для записей разговоров настроено **простое автоматическое удаление** через S3 Lifecycle Policies. Это позволяет:
- ✅ Автоматически удалять старые файлы через 2 года
- ⚡ **Мгновенный доступ** ко всем файлам весь срок жизни
- 🔒 Соблюдать требования по хранению данных
- ⚙️ Работать без изменений в коде приложения
- 🚀 **БЕЗ задержек** при скачивании (файлы остаются в S3 Standard)

---

## 🏗️ Где хранятся настройки

### 📍 Местоположение настроек
Настройки автоудаления хранятся **на уровне S3 bucket** в виде **Lifecycle Policies**.

**Bucket**: `vochi`  
**Провайдер**: Hetzner Object Storage  
**Endpoint**: `https://fsn1.your-objectstorage.com`

### 🔧 Как получить доступ к настройкам

#### Через AWS CLI:
```bash
# Просмотр текущих правил
aws s3api get-bucket-lifecycle-configuration \
  --bucket vochi \
  --endpoint-url https://fsn1.your-objectstorage.com

# Удаление всех правил (ОСТОРОЖНО!)
aws s3api delete-bucket-lifecycle \
  --bucket vochi \
  --endpoint-url https://fsn1.your-objectstorage.com
```

#### Через boto3 (Python):
```python
import boto3
from s3_config import S3_CONFIG

s3_client = boto3.client(
    's3',
    endpoint_url=f"https://{S3_CONFIG['REGION']}.your-objectstorage.com",
    aws_access_key_id=S3_CONFIG['ACCESS_KEY'],
    aws_secret_access_key=S3_CONFIG['SECRET_KEY'],
    region_name=S3_CONFIG['REGION']
)

# Получить правила
response = s3_client.get_bucket_lifecycle_configuration(Bucket='vochi')
print(response['Rules'])
```

---

## ⚙️ Текущие настройки

### 🎯 Действующие правила (обновлено 29.07.2025)

#### ✅ **Правило: CallRecordingsSimpleDelete** (АКТИВНОЕ)
```json
{
    "ID": "CallRecordingsSimpleDelete",
    "Status": "Enabled",
    "Filter": {
        "Prefix": "CallRecords/"
    },
    "Expiration": {
        "Days": 730
    }
}
```
**Действие**: 
- ✅ Удаляет файлы в папке `CallRecords/` через **730 дней (2 года)**
- ⚡ **Мгновенный доступ** весь срок жизни (S3 Standard)
- 🚀 **БЕЗ задержек** при скачивании
- ❌ **НЕТ переходов** между классами хранения

#### ❌ **Старые правила: CallRecordingsTransitionToIA** (УДАЛЕНЫ)
```json
{
    "ID": "CallRecordingsTransitionToIA",
    "Status": "Enabled",
    "Filter": {
        "Prefix": "CallRecords/"
    },
    "Transitions": [
        {
            "Days": 30,
            "StorageClass": "STANDARD_IA"
        },
        {
            "Days": 90,
            "StorageClass": "GLACIER"
        }
    ]
}
```
**Действие**: Переводит файлы в дешевые классы хранения

---

## 📊 Простой жизненный цикл файла записи

```
📁 Загрузка на S3
    ↓
⚡ S3 STANDARD (0-730 дней)
    ├─ Мгновенный доступ (всегда)
    ├─ Никаких переходов между классами
    ├─ БЕЗ задержек при скачивании
    └─ Максимальная производительность
    ↓ (через 730 дней = 2 года)
🗑️ АВТОМАТИЧЕСКОЕ УДАЛЕНИЕ
```

### 🎯 Приоритеты:
- ✅ **Скорость доступа** > экономия на хранении
- ✅ **Надежность** > стоимость
- ✅ **Простота** > сложные схемы

---

## 🛠️ Управление правилами

### 📝 Создание скрипта для изменения настроек

```python
#!/usr/bin/env python3
"""
Скрипт для управления lifecycle policies
"""

import boto3
from s3_config import S3_CONFIG

def update_lifecycle_policy(days_to_expire=730):
    """Обновляет правила автоудаления"""
    
    s3_client = boto3.client(
        's3',
        endpoint_url=f"https://{S3_CONFIG['REGION']}.your-objectstorage.com",
        aws_access_key_id=S3_CONFIG['ACCESS_KEY'],
        aws_secret_access_key=S3_CONFIG['SECRET_KEY'],
        region_name=S3_CONFIG['REGION']
    )
    
    lifecycle_config = {
        'Rules': [
            {
                'ID': 'CallRecordingsAutoDelete',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'CallRecords/'},
                'Expiration': {'Days': days_to_expire}
            },
            {
                'ID': 'CallRecordingsTransitionToIA',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'CallRecords/'},
                'Transitions': [
                    {'Days': 30, 'StorageClass': 'STANDARD_IA'},
                    {'Days': 90, 'StorageClass': 'GLACIER'}
                ]
            }
        ]
    }
    
    s3_client.put_bucket_lifecycle_configuration(
        Bucket='vochi',
        LifecycleConfiguration=lifecycle_config
    )
    
    print(f"✅ Правила обновлены: удаление через {days_to_expire} дней")

if __name__ == "__main__":
    # Изменить на нужное количество дней
    update_lifecycle_policy(days_to_expire=365)  # 1 год
```

### 🔍 Проверка текущих правил

```python
def check_lifecycle_rules():
    """Проверяет текущие правила lifecycle"""
    
    s3_client = boto3.client(
        's3',
        endpoint_url=f"https://{S3_CONFIG['REGION']}.your-objectstorage.com",
        aws_access_key_id=S3_CONFIG['ACCESS_KEY'],
        aws_secret_access_key=S3_CONFIG['SECRET_KEY'],
        region_name=S3_CONFIG['REGION']
    )
    
    try:
        response = s3_client.get_bucket_lifecycle_configuration(Bucket='vochi')
        
        print("📋 ТЕКУЩИЕ LIFECYCLE ПРАВИЛА:")
        print("="*50)
        
        for rule in response['Rules']:
            print(f"🔸 Правило: {rule['ID']}")
            print(f"  Статус: {rule['Status']}")
            if 'Filter' in rule and 'Prefix' in rule['Filter']:
                print(f"  Путь: {rule['Filter']['Prefix']}")
            if 'Expiration' in rule:
                print(f"  Удаление: через {rule['Expiration']['Days']} дней")
            if 'Transitions' in rule:
                print("  Переходы:")
                for transition in rule['Transitions']:
                    print(f"    • {transition['Days']} дней → {transition['StorageClass']}")
            print()
            
    except s3_client.exceptions.NoSuchLifecycleConfiguration:
        print("ℹ️  Lifecycle policy не настроена")
        return False
        
    return True
```

---

## 🚨 Важные моменты

### ⚠️ Предупреждения
1. **Необратимость**: Удаленные файлы восстановить нельзя
2. **Применение правил**: Изменения могут занять до 24 часов
3. **Проверка перед изменением**: Всегда тестируйте на тестовых данных

### 🔒 Безопасность
- Правила применяются только к папке `CallRecords/`
- Другие файлы в S3 не затрагиваются
- Изменения требуют прав доступа к S3

### 💡 Рекомендации
- **2 года (730 дней)** - оптимальный срок для большинства требований
- **1 год (365 дней)** - для экономии места
- **3 года (1095 дней)** - для соблюдения регуляторных требований

---

## 📞 Связь с кодом приложения

### 🔗 Где используется в коде

#### В `daily_recordings_sync.py`:
```python
# НЕТ изменений - правила работают автоматически
self.s3_client.s3_client.upload_file(
    mp3_file, 
    self.s3_client.bucket_name, 
    object_key,
    ExtraArgs={
        'Metadata': {...},
        'ContentType': 'audio/mpeg'
        # Lifecycle правила применяются автоматически
    }
)
```

#### В других сервисах загрузки:
- `call_download.py` - не требует изменений
- `recording_downloader.py` - не требует изменений  
- `auto_download_0335.py` - не требует изменений

### 🎯 Альтернативный способ (на уровне файла)

Если нужно задать срок жизни для конкретного файла:

```python
from datetime import datetime, timedelta

# При загрузке файла
ExtraArgs={
    'Metadata': {...},
    'ContentType': 'audio/mpeg',
    'Expires': datetime.now() + timedelta(days=365)  # Удалить через год
}
```

---

## 🧪 Тестирование и мониторинг

### ✅ Как проверить работу правил

1. **Создать тестовый файл**:
```python
# Загрузить файл с датой в прошлом (симуляция)
test_key = f"CallRecords/test/2023/01/test_old_file.mp3"
s3_client.upload_file(local_file, bucket, test_key)
```

2. **Проверить статус**:
```bash
aws s3api head-object \
  --bucket vochi \
  --key "CallRecords/test/2023/01/test_old_file.mp3" \
  --endpoint-url https://fsn1.your-objectstorage.com
```

3. **Мониторинг через CloudWatch** (если доступен):
- Метрики удаления объектов
- Метрики изменения класса хранения

---

## 📈 Экономия средств

### 💰 Примерная экономия
При объеме **1 ТБ записей в месяц**:

| Период | Класс хранения | Стоимость/ГБ/мес | Экономия |
|--------|---------------|------------------|----------|
| 0-29 дней | STANDARD | €0.021 | - |
| 30-89 дней | STANDARD_IA | €0.012 | ~45% |
| 90-729 дней | GLACIER | €0.007 | ~68% |
| 730+ дней | УДАЛЕН | €0.000 | 100% |

**Годовая экономия**: до 40-50% от стоимости хранения.

---

## 🔄 История изменений

| Дата | Действие | Детали |
|------|----------|--------|
| 29.07.2025 | Настройка | Установлены lifecycle policies: удаление через 730 дней |
| 29.07.2025 | Оптимизация | Добавлены переходы STANDARD_IA (30д) и GLACIER (90д) |

---

## 📞 Контакты и поддержка

При вопросах по настройке автоудаления:
1. Проверить текущие правила через скрипт выше
2. Убедиться в доступности S3 credentials
3. Проверить права доступа к bucket

**Файлы конфигурации**:
- `s3_config.py` - настройки подключения к S3
- `db_readme.txt` - общие настройки подключений

**Логи**:
- Проверять логи `daily_recordings_sync.py` на предмет ошибок загрузки
- S3 не логирует действия lifecycle напрямую в приложение 