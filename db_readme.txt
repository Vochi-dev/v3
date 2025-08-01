### Команды для анализа PostgreSQL

**1. Проверка соединения и базовой информации**

*   **Список всех баз данных на сервере:**
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -l
    ```
*   **Список всех схем в конкретной базе данных:**
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d [имя_базы] -c "\dn"
    ```
*   **Простейший тест на успешное подключение к базе:**
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d [имя_базы] -c "SELECT 1;"
    ```

**2. Получение структуры таблиц (основной способ)**

*   **Вывести структуру конкретной таблицы (столбцы, типы, индексы, ключи):**
    *Этот способ оказался самым надежным в итоге.*
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d [имя_базы] -c '\d "имя_таблицы"'
    ```
    *Примеры, которые я использовал:*
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d postgres -c '\d "goip"'
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d postgres -c '\d "gsm_lines"'
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d postgres -c '\d "gsm_outgoing_schema_assignments"'
    ```

**3. Получение структуры (альтернативные способы через `information_schema`)**

*   **Вывести список всех таблиц в схеме `public`:**
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d [имя_базы] -c "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
    ```
*   **Вывести столбцы и их типы для конкретной таблицы:**
    ```bash
    PGPASSWORD='[ВАШ_ПАРОЛЬ]' psql -U postgres -d [имя_базы] -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'имя_таблицы' ORDER BY ordinal_position;"
    ```

# Учетные данные для подключения к БД
Port: 5432
Database: postgres
Username: postgres
Password: r/Yskqh/ZbZuvjb2b3ahfg== 

# Учетные данные для подключения к Hetzner Object Storage (S3)
Endpoint: fsn1.your-objectstorage.com
Bucket: vochi
Region: fsn1
Access Key: P2Y51APGUNH4785SIT4M
Secret Key: ggVFRYaAo9Yv5mhualQnUA2nEqGeWF9Dfh1IGMmZ
URL Bucket: https://vochi.fsn1.your-objectstorage.com/

### Команды для работы с Object Storage

**1. Тестирование подключения к S3**

*   **Полный тест подключения:**
    ```bash
    python3 test_s3_connection.py
    ```

*   **Быстрая проверка подключения через python:**
    ```bash
    python3 -c "
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    s3 = HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY'])
    usage = s3.get_storage_usage()
    print(f'Файлов: {usage[\"total_files\"]}, Размер: {usage[\"total_size_mb\"]} MB')
    "
    ```

**2. Загрузка записей разговоров**

*   **Загрузить запись разговора для предприятия:**
    ```bash
    python3 -c "
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    from datetime import datetime
    
    s3 = HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY'])
    file_url = s3.upload_call_recording(
        enterprise_number='0387',
        call_unique_id='call_123456',
        local_file_path='/path/to/recording.wav'
    )
    print(f'Загружено: {file_url}')
    "
    ```

*   **Пример загрузки через curl (альтернатива):**
    ```bash
    curl "https://vochi.fsn1.your-objectstorage.com/call-recordings/2025/07/23/0387/test.wav" \
      -T "local_file.wav" \
      --user "P2Y51APGUNH4785SIT4M:ggVFRYaAo9Yv5mhualQnUA2nEqGeWF9Dfh1IGMmZ" \
      --aws-sigv4 "aws:amz:fsn1:s3"
    ```

**3. Поиск и получение записей**

*   **Найти записи предприятия за период:**
    ```bash
    python3 -c "
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    from datetime import datetime, timedelta
    
    s3 = HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY'])
    recordings = s3.find_recordings(
        enterprise_number='0387',
        date_from=datetime.now() - timedelta(days=7),
        date_to=datetime.now()
    )
    
    for r in recordings:
        print(f'{r[\"key\"]} - {r[\"size\"]} байт')
    "
    ```

*   **Создать временную ссылку для скачивания (24 часа):**
    ```bash
    python3 -c "
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    
    s3 = HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY'])
    link = s3.generate_download_link(
        'call-recordings/2025/07/23/0387/call_123456.wav',
        expires_in=86400
    )
    print(f'Временная ссылка: {link}')
    "
    ```

**4. Управление хранилищем**

*   **Получить статистику использования:**
    ```bash
    python3 -c "
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    
    s3 = HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY'])
    usage = s3.get_storage_usage()
    print(f'Всего файлов: {usage[\"total_files\"]}')
    print(f'Размер: {usage[\"total_size_gb\"]} GB')
    print(f'Стоимость ~{max(4.99, usage[\"total_size_gb\"] * 4.99):.2f} EUR/месяц')
    "
    ```

*   **Удалить записи старше 90 дней:**
    ```bash
    python3 -c "
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    
    s3 = HetznerS3Client(S3_CONFIG['ACCESS_KEY'], S3_CONFIG['SECRET_KEY'])
    deleted = s3.delete_old_recordings(days_to_keep=90)
    print(f'Удалено старых записей: {deleted}')
    "
    ```

**5. Работа через curl (прямые S3 команды)**

*   **Список всех файлов в bucket:**
    ```bash
    curl -sS "https://fsn1.your-objectstorage.com/" \
      --user "P2Y51APGUNH4785SIT4M:ggVFRYaAo9Yv5mhualQnUA2nEqGeWF9Dfh1IGMmZ" \
      --aws-sigv4 "aws:amz:fsn1:s3"
    ```

*   **Список файлов в bucket vochi:**
    ```bash
    curl -sS "https://vochi.fsn1.your-objectstorage.com" \
      --user "P2Y51APGUNH4785SIT4M:ggVFRYaAo9Yv5mhualQnUA2nEqGeWF9Dfh1IGMmZ" \
      --aws-sigv4 "aws:amz:fsn1:s3"
    ```

*   **Скачать файл:**
    ```bash
    curl "https://vochi.fsn1.your-objectstorage.com/call-recordings/path/to/file.wav" \
      -o "downloaded_file.wav" \
      --user "P2Y51APGUNH4785SIT4M:ggVFRYaAo9Yv5mhualQnUA2nEqGeWF9Dfh1IGMmZ" \
      --aws-sigv4 "aws:amz:fsn1:s3"
    ```

### Структура хранения записей в S3:
```
vochi/
└── call-recordings/
    └── ГГГГ/           # Год
        └── ММ/         # Месяц  
            └── ДД/     # День
                └── XXXX/   # Номер предприятия
                    ├── call_unique_id_1.wav
                    ├── call_unique_id_2.wav
                    └── ...

Пример:
vochi/call-recordings/2025/07/23/0387/call_1753256669.wav
```

### Интеграция с основным проектом:

*   **Подключение S3 клиента в коде:**
    ```python
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG
    
    # Создание клиента
    s3_client = HetznerS3Client(
        access_key=S3_CONFIG['ACCESS_KEY'],
        secret_key=S3_CONFIG['SECRET_KEY']
    )
    ```

*   **Автоматическая выгрузка записей (для добавления в cron):**
    ```bash
    # Каждый день в 23:00 выгружать записи в S3 и удалять локальные
    0 23 * * * cd /root/asterisk-webhook && python3 scripts/daily_backup_to_s3.py >> logs/s3_backup.log 2>&1
    ``` ...