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