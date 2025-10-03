#!/bin/bash

# ===============================================
# СКРИПТ УПРАВЛЕНИЯ ПАРТИЦИЯМИ CALL LOGGER
# ===============================================

LOGGER_URL="http://localhost:8026"

case "$1" in
    list)
        echo "📋 Список всех партиций:"
        echo ""
        echo "🗂️ Простая схема: одна партиция = один номер предприятия"
        PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "
        SELECT 
            SUBSTRING(tablename FROM 'call_traces_(.*)') as enterprise_number,
            tablename as partition_name,
            pg_size_pretty(pg_total_relation_size('public.'||tablename)) as size,
            (SELECT COUNT(*) FROM call_traces WHERE tableoid = ('public.'||tablename)::regclass) as records
        FROM pg_tables 
        WHERE tablename LIKE 'call_traces_0%' 
        ORDER BY enterprise_number;
        "
        ;;
        
    create)
        echo "ℹ️  В упрощенной схеме партиции создаются автоматически"
        echo "🗂️ Используются 4 партиции по предприятиям: 0367, 0280, 0368, 0286"
        echo "📊 Новые предприятия попадают в одну из существующих партиций"
        echo ""
        echo "Для добавления новой партиции предприятия:"
        echo "  $0 add-enterprise 0100"
        ;;
        
    delete)
        echo "⚠️  В упрощенной схеме нельзя удалять отдельные партиции предприятий"
        echo "🗂️ Все данные хранятся в 4 общих партициях"
        echo ""
        echo "Для очистки данных конкретного предприятия используйте:"
        echo "  $0 cleanup 0367"
        echo ""
        echo "Для полной очистки всех данных:"
        echo "  $0 cleanup-all"
        ;;
        
    stats)
        if [ -z "$2" ]; then
            echo "❌ Укажите номер предприятия"
            echo "Использование: $0 stats 0367"
            exit 1
        fi
        
        ENTERPRISE="$2"
        echo "📊 Статистика предприятия $ENTERPRISE:"
        echo ""
        PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "
        SELECT 
            COUNT(*) as total_calls,
            COUNT(CASE WHEN call_events @> '[{\"event_type\": \"hangup\"}]' THEN 1 END) as completed_calls,
            AVG(jsonb_array_length(call_events)) as avg_events_per_call,
            MIN(start_time) as first_call,
            MAX(start_time) as last_call,
            pg_size_pretty(SUM(pg_column_size(call_events))) as events_size
        FROM call_traces 
        WHERE enterprise_number = '$ENTERPRISE';
        "
        ;;
        
    health)
        echo "🏥 Проверка здоровья logger сервиса:"
        echo ""
        curl -s "$LOGGER_URL/health" | python3 -m json.tool
        ;;
        
    rebuild-partitions)
        echo "🔄 Пересоздание партиций call_traces с понятными названиями..."
        echo ""
        
        PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "
        -- Удаляем старые партиции если есть
        DROP TABLE IF EXISTS call_traces_0367, call_traces_0280, call_traces_0368, call_traces_other CASCADE;
        
        -- Создаем партиции с правильными remainder'ами (проверено тестированием)
        CREATE TABLE call_traces_0368 PARTITION OF call_traces
            FOR VALUES WITH (MODULUS 4, REMAINDER 0);  -- 0368, 0286
        
        CREATE TABLE call_traces_0367 PARTITION OF call_traces
            FOR VALUES WITH (MODULUS 4, REMAINDER 1);  -- 0367
        
        CREATE TABLE call_traces_0280 PARTITION OF call_traces
            FOR VALUES WITH (MODULUS 4, REMAINDER 2);  -- 0280
        
        CREATE TABLE call_traces_other PARTITION OF call_traces
            FOR VALUES WITH (MODULUS 4, REMAINDER 3);  -- будущие предприятия
        "
        
        if [ $? -eq 0 ]; then
            echo "✅ Партиции пересозданы с понятными названиями!"
        else
            echo "❌ Ошибка пересоздания партиций"
        fi
        ;;
        
    add-enterprise)
        if [ -z "$2" ]; then
            echo "❌ Укажите номер предприятия"
            echo "Использование: $0 add-enterprise 0100"
            exit 1
        fi
        
        ENTERPRISE="$2"
        
        # Проверяем есть ли уже партиция для этого предприятия
        PARTITION_EXISTS=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "
        SELECT COUNT(*) FROM pg_tables WHERE tablename = '$ENTERPRISE' AND schemaname = 'public';
        ")
        PARTITION_EXISTS=$(echo $PARTITION_EXISTS | tr -d ' ')
        
        if [ "$PARTITION_EXISTS" = "1" ]; then
            echo "✅ Партиция $ENTERPRISE уже существует"
            
            # Показываем статистику
            RECORDS=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "
            SELECT COUNT(*) FROM \"$ENTERPRISE\";
            ")
            echo "📊 Записей в партиции: $(echo $RECORDS | tr -d ' ')"
        else
            echo "➕ Создание партиции для предприятия $ENTERPRISE..."
            
            PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "
            CREATE TABLE \"$ENTERPRISE\" PARTITION OF call_traces FOR VALUES IN ('$ENTERPRISE');
            ALTER TABLE \"$ENTERPRISE\" ADD CONSTRAINT unique_call_trace_$ENTERPRISE UNIQUE (unique_id, enterprise_number);
            "
            
            if [ $? -eq 0 ]; then
                echo "✅ Партиция $ENTERPRISE создана успешно!"
                echo "📝 Теперь можно использовать: SELECT * FROM \"$ENTERPRISE\";"
            else
                echo "❌ Ошибка создания партиции"
            fi
        fi
        ;;
        
    sql)
        if [ -z "$2" ]; then
            echo "❌ Укажите SQL команду"
            echo "Использование: $0 sql \"SELECT COUNT(*) FROM call_traces_0367;\""
            exit 1
        fi
        
        SQL_QUERY="$2"
        echo "🗃️  Выполнение SQL запроса:"
        echo "   $SQL_QUERY"
        echo ""
        
        PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "$SQL_QUERY"
        ;;
        
    cleanup)
        if [ -z "$2" ]; then
            echo "❌ Укажите номер предприятия"
            echo "Использование: $0 cleanup 0367"
            exit 1
        fi
        
        ENTERPRISE="$2"
        echo "🗑️  Очистка данных предприятия $ENTERPRISE..."
        
        PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "
        DELETE FROM call_traces WHERE enterprise_number = '$ENTERPRISE';
        "
        
        DELETED=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "SELECT ROW_COUNT();")
        echo "✅ Удалено записей: $DELETED"
        ;;
        
    cleanup-all)
        echo "⚠️  ВНИМАНИЕ! Это удалит ВСЕ данные из call_traces!"
        echo "Вы уверены? (yes/no)"
        read -r CONFIRM
        
        if [ "$CONFIRM" = "yes" ]; then
            echo "🗑️  Очистка всех данных..."
            PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -c "
            TRUNCATE call_traces;
            "
            echo "✅ Все данные удалены"
        else
            echo "❌ Операция отменена"
        fi
        ;;
        
    test-event)
        ENTERPRISE="${2:-0367}"
        UNIQUE_ID="TEST-$(date +%s).0"
        
        echo "🧪 Тестирование логирования события для предприятия $ENTERPRISE..."
        echo "📋 UniqueId: $UNIQUE_ID"
        
        RESPONSE=$(curl -s -X POST "$LOGGER_URL/log/event" -H "Content-Type: application/json" -d "{
            \"enterprise_number\": \"$ENTERPRISE\",
            \"unique_id\": \"$UNIQUE_ID\",
            \"event_type\": \"dial\",
            \"event_data\": {
                \"Phone\": \"375296254070\",
                \"Extensions\": [\"150\"],
                \"Trunk\": \"0001363\"
            }
        }")
        
        echo "$RESPONSE" | python3 -m json.tool
        
        if echo "$RESPONSE" | grep -q '"status": "success"'; then
            echo ""
            echo "✅ Событие залогировано! Получаем трейс:"
            echo ""
            curl -s "$LOGGER_URL/trace/$UNIQUE_ID" | python3 -m json.tool
        fi
        ;;
        
    *)
        echo "🔧 Управление Call Logger Service (Упрощенная схема)"
        echo ""
        echo "Использование: $0 {команда} [параметры]"
        echo ""
        echo "📊 Информация:"
        echo "  list                    - Список всех партиций"
        echo "  stats <enterprise>      - Статистика по предприятию"
        echo "  health                  - Проверка здоровья сервиса"
        echo ""
        echo "🔧 Управление:"
        echo "  rebuild-partitions      - Пересоздать партиции с понятными названиями"
        echo "  add-enterprise <num>    - Добавить партицию для нового предприятия"
        echo "  cleanup <enterprise>    - Очистить данные предприятия"
        echo "  cleanup-all            - Очистить все данные (с подтверждением)"
        echo ""
        echo "🧪 Тестирование:"
        echo "  test-event [enterprise] - Тест логирования (по умолчанию 0367)"
        echo "  sql \"<query>\"           - Выполнить SQL запрос"
        echo ""
        echo "📋 Примеры:"
        echo "  $0 list"
        echo "  $0 stats 0367"
        echo "  $0 add-enterprise 0100"
        echo "  $0 test-event 0367"
        echo "  $0 cleanup 0367"
        echo "  $0 sql \"SELECT COUNT(*) FROM call_traces_0367;\""
        echo ""
        echo "ℹ️  Схема с понятными названиями (проверено тестированием):"
        echo "  - call_traces_0368 (remainder 0): предприятия 0368, 0286"
        echo "  - call_traces_0367 (remainder 1): предприятие 0367"  
        echo "  - call_traces_0280 (remainder 2): предприятие 0280"
        echo "  - call_traces_other (remainder 3): будущие предприятия"
        echo "  - События хранятся в JSONB поле call_events"
        echo ""
        echo "Сервис: $LOGGER_URL"
        exit 1
        ;;
esac

exit 0
