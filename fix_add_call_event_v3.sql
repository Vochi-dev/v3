-- =====================================================
-- ИСПРАВЛЕНИЕ ФУНКЦИИ add_call_event V3
-- Дата: 2025-11-29
-- Цель: ОДИН ЗВОНОК = ОДНА ЗАПИСЬ
-- =====================================================
-- 
-- АЛГОРИТМ ПОИСКА ЗАПИСИ (приоритет):
-- 1. По BridgeUniqueid (главный ключ после события bridge)
-- 2. По UniqueId (точное совпадение)
-- 3. По UniqueId в related_unique_ids
-- 4. По Phone + временное окно 5 минут (для dial/new_callerid)
-- 5. Создать новую запись
--
-- СВЯЗУЮЩИЕ ИДЕНТИФИКАТОРЫ:
-- - Phone (375296254070) - связывает dial и new_callerid
-- - BridgeUniqueid - связывает все события после bridge_create
-- - UniqueId - идентификатор канала
-- =====================================================

CREATE OR REPLACE FUNCTION add_call_event(
    p_unique_id VARCHAR,
    p_enterprise_number VARCHAR,
    p_event_type VARCHAR,
    p_event_data JSONB,
    p_phone_number VARCHAR DEFAULT NULL,
    p_bridge_unique_id VARCHAR DEFAULT NULL
) RETURNS BIGINT AS $$
DECLARE
    v_trace_id BIGINT;
    v_other_trace_id BIGINT;
    v_call_direction VARCHAR(10);
    v_call_status VARCHAR(20);
    v_existing_events JSONB;
    v_current_event_sequence INT;
    v_new_related_ids JSONB;
    v_other_events JSONB;
    v_other_telegram JSONB;
    v_other_related JSONB;
    v_other_http JSONB;
    v_other_sql JSONB;
    v_other_integration JSONB;
    v_end_time TIMESTAMPTZ;
    v_start_time TIMESTAMPTZ;
    v_extracted_phone VARCHAR(20);
BEGIN
    -- Определяем направление и статус звонка
    IF p_event_type IN ('start', 'dial') THEN
        v_call_direction := 'outgoing';
        v_call_status := 'active';
    ELSIF p_event_type = 'hangup' THEN
        v_call_status := 'completed';
        -- Извлекаем StartTime и EndTime из hangup
        BEGIN
            v_start_time := (p_event_data->>'StartTime')::timestamptz;
        EXCEPTION WHEN OTHERS THEN
            v_start_time := NULL;
        END;
        BEGIN
            v_end_time := (p_event_data->>'EndTime')::timestamptz;
        EXCEPTION WHEN OTHERS THEN
            v_end_time := NULL;
        END;
    END IF;

    -- Извлекаем BridgeUniqueid из event_data если не передан
    IF p_bridge_unique_id IS NULL OR p_bridge_unique_id = '' THEN
        p_bridge_unique_id := p_event_data->>'BridgeUniqueid';
        IF p_bridge_unique_id = '' THEN
            p_bridge_unique_id := NULL;
        END IF;
    END IF;

    -- Извлекаем Phone из разных полей event_data
    v_extracted_phone := COALESCE(
        p_phone_number,
        p_event_data->>'Phone',
        p_event_data->>'CallerIDNum',
        p_event_data->>'Exten'
    );
    -- Исключаем внутренние номера (3-4 цифры)
    IF v_extracted_phone IS NOT NULL AND length(v_extracted_phone) <= 4 THEN
        v_extracted_phone := NULL;
    END IF;
    -- Используем извлечённый phone если p_phone_number не передан
    IF p_phone_number IS NULL THEN
        p_phone_number := v_extracted_phone;
    END IF;

    v_trace_id := NULL;

    -- =====================================================
    -- ШАГ 1: Ищем по BridgeUniqueid (ПРИОРИТЕТ!)
    -- =====================================================
    IF p_bridge_unique_id IS NOT NULL THEN
        SELECT id INTO v_trace_id
        FROM call_traces
        WHERE bridge_unique_id = p_bridge_unique_id
          AND enterprise_number = p_enterprise_number
        LIMIT 1;
    END IF;

    -- =====================================================
    -- ШАГ 2: Ищем по UniqueId
    -- =====================================================
    IF v_trace_id IS NULL AND p_unique_id IS NOT NULL AND p_unique_id != '' THEN
        SELECT id INTO v_trace_id
        FROM call_traces
        WHERE unique_id = p_unique_id
          AND enterprise_number = p_enterprise_number
        LIMIT 1;
    END IF;

    -- =====================================================
    -- ШАГ 3: Ищем по UniqueId в related_unique_ids
    -- =====================================================
    IF v_trace_id IS NULL AND p_unique_id IS NOT NULL AND p_unique_id != '' THEN
        SELECT id INTO v_trace_id
        FROM call_traces
        WHERE related_unique_ids @> to_jsonb(p_unique_id)
          AND enterprise_number = p_enterprise_number
        LIMIT 1;
    END IF;

    -- =====================================================
    -- ШАГ 4: Ищем по Phone + временное окно (для dial/new_callerid)
    -- =====================================================
    IF v_trace_id IS NULL 
       AND v_extracted_phone IS NOT NULL 
       AND p_event_type IN ('dial', 'new_callerid', 'start') THEN
        SELECT id INTO v_trace_id
        FROM call_traces
        WHERE phone_number = v_extracted_phone
          AND enterprise_number = p_enterprise_number
          AND created_at > NOW() - INTERVAL '5 minutes'
        ORDER BY created_at DESC
        LIMIT 1;
    END IF;

    -- =====================================================
    -- ОБНОВЛЕНИЕ или СОЗДАНИЕ записи
    -- =====================================================
    IF v_trace_id IS NOT NULL THEN
        -- Обновляем существующую запись
        
        -- Собираем related_unique_ids
        SELECT COALESCE(related_unique_ids, '[]'::jsonb) INTO v_new_related_ids
        FROM call_traces WHERE id = v_trace_id;

        -- Добавляем UniqueId если его нет
        IF p_unique_id IS NOT NULL AND p_unique_id != '' AND NOT (v_new_related_ids @> to_jsonb(p_unique_id)) THEN
            v_new_related_ids := v_new_related_ids || to_jsonb(ARRAY[p_unique_id]);
        END IF;

        -- Добавляем BridgeUniqueid если его нет
        IF p_bridge_unique_id IS NOT NULL AND NOT (v_new_related_ids @> to_jsonb(p_bridge_unique_id)) THEN
            v_new_related_ids := v_new_related_ids || to_jsonb(ARRAY[p_bridge_unique_id]);
        END IF;

        -- Обновляем основные поля
        UPDATE call_traces
        SET
            related_unique_ids = v_new_related_ids,
            bridge_unique_id = COALESCE(bridge_unique_id, p_bridge_unique_id),
            phone_number = COALESCE(phone_number, p_phone_number),
            call_direction = COALESCE(call_direction, v_call_direction),
            call_status = COALESCE(v_call_status, call_status),
            start_time = COALESCE(v_start_time, start_time),
            end_time = COALESCE(v_end_time, end_time)
        WHERE id = v_trace_id;

        -- Добавляем событие
        SELECT call_events INTO v_existing_events FROM call_traces WHERE id = v_trace_id;
        v_current_event_sequence := jsonb_array_length(COALESCE(v_existing_events, '[]'::jsonb)) + 1;

        UPDATE call_traces SET
            call_events = COALESCE(call_events, '[]'::jsonb) || jsonb_build_array(jsonb_build_object(
                'event_sequence', v_current_event_sequence,
                'event_type', p_event_type,
                'event_timestamp', NOW(),
                'unique_id', p_unique_id,
                'event_data', p_event_data
            )),
            updated_at = NOW()
        WHERE id = v_trace_id;

        -- =====================================================
        -- ОБЪЕДИНЕНИЕ записей с одинаковым BridgeUniqueid
        -- =====================================================
        IF p_bridge_unique_id IS NOT NULL THEN
            FOR v_other_trace_id IN
                SELECT id FROM call_traces
                WHERE bridge_unique_id = p_bridge_unique_id
                  AND enterprise_number = p_enterprise_number
                  AND id != v_trace_id
            LOOP
                -- Получаем данные из другой записи
                SELECT
                    COALESCE(call_events, '[]'::jsonb),
                    COALESCE(telegram_messages, '[]'::jsonb),
                    COALESCE(related_unique_ids, '[]'::jsonb),
                    COALESCE(http_requests, '[]'::jsonb),
                    COALESCE(sql_queries, '[]'::jsonb),
                    COALESCE(integration_responses, '[]'::jsonb)
                INTO v_other_events, v_other_telegram, v_other_related, v_other_http, v_other_sql, v_other_integration
                FROM call_traces WHERE id = v_other_trace_id;

                -- Объединяем все данные в основную запись
                UPDATE call_traces
                SET
                    call_events = COALESCE(call_events, '[]'::jsonb) || v_other_events,
                    telegram_messages = COALESCE(telegram_messages, '[]'::jsonb) || v_other_telegram,
                    http_requests = COALESCE(http_requests, '[]'::jsonb) || v_other_http,
                    sql_queries = COALESCE(sql_queries, '[]'::jsonb) || v_other_sql,
                    integration_responses = COALESCE(integration_responses, '[]'::jsonb) || v_other_integration,
                    related_unique_ids = (
                        SELECT jsonb_agg(DISTINCT e)
                        FROM jsonb_array_elements(COALESCE(related_unique_ids, '[]'::jsonb) || v_other_related) e
                    )
                WHERE id = v_trace_id;

                -- Удаляем дубликат
                DELETE FROM call_traces WHERE id = v_other_trace_id;
            END LOOP;
        END IF;

    ELSE
        -- Создаём новую запись (только если есть UniqueId)
        IF p_unique_id IS NOT NULL AND p_unique_id != '' THEN
            BEGIN
                INSERT INTO call_traces (
                    unique_id, enterprise_number, call_events, phone_number,
                    call_direction, call_status, bridge_unique_id, related_unique_ids,
                    start_time, end_time
                )
                VALUES (
                    p_unique_id, p_enterprise_number,
                    jsonb_build_array(jsonb_build_object(
                        'event_sequence', 1,
                        'event_type', p_event_type,
                        'event_timestamp', NOW(),
                        'unique_id', p_unique_id,
                        'event_data', p_event_data
                    )),
                    p_phone_number, v_call_direction, v_call_status, p_bridge_unique_id,
                    to_jsonb(ARRAY[p_unique_id]) ||
                    CASE WHEN p_bridge_unique_id IS NOT NULL THEN to_jsonb(ARRAY[p_bridge_unique_id]) ELSE '[]'::jsonb END,
                    v_start_time, v_end_time
                )
                RETURNING id INTO v_trace_id;
            EXCEPTION
                WHEN unique_violation THEN
                    -- Race condition - находим запись и вызываем рекурсивно
                    SELECT id INTO v_trace_id
                    FROM call_traces
                    WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
                    LIMIT 1;
                    IF v_trace_id IS NOT NULL THEN
                        RETURN add_call_event(p_unique_id, p_enterprise_number, p_event_type, p_event_data, p_phone_number, p_bridge_unique_id);
                    END IF;
            END;
        END IF;
    END IF;

    RETURN v_trace_id;
END;
$$ LANGUAGE plpgsql;

-- Комментарий к функции
COMMENT ON FUNCTION add_call_event IS 'V3 2025-11-29: Добавлен поиск по Phone для связи dial/new_callerid. Алгоритм: 1.BridgeUniqueid 2.UniqueId 3.related_unique_ids 4.Phone+5min';

