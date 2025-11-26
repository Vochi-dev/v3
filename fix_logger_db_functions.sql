-- ═══════════════════════════════════════════════════════════════════
-- ИСПРАВЛЕНИЕ ФУНКЦИЙ LOGGER ДЛЯ ОБРАБОТКИ ДУБЛИКАТОВ
-- Дата: 2025-11-18
-- Проблема: duplicate key value violates unique constraint
-- Решение: Добавить ON CONFLICT DO NOTHING в INSERT
-- ═══════════════════════════════════════════════════════════════════

-- 1. ФУНКЦИЯ: add_call_event
-- Исправление: Добавлен ON CONFLICT DO NOTHING в INSERT
CREATE OR REPLACE FUNCTION public.add_call_event(
    p_unique_id character varying, 
    p_enterprise_number character varying, 
    p_event_type character varying, 
    p_event_data jsonb, 
    p_phone_number character varying DEFAULT NULL::character varying, 
    p_bridge_unique_id character varying DEFAULT NULL::character varying
)
RETURNS bigint
LANGUAGE plpgsql
AS $function$
DECLARE
    v_trace_id BIGINT;
    v_call_direction VARCHAR(10);
    v_call_status VARCHAR(20);
    v_my_record_id BIGINT;
    v_bridge_record_id BIGINT;
    v_my_events JSONB;
    v_my_related_uids JSONB;
    v_my_phone VARCHAR(20);
    v_my_direction VARCHAR(10);
    v_my_telegram JSONB;
    v_my_http JSONB;
    v_my_sql JSONB;
    v_my_integration JSONB;
BEGIN
    -- Определяем направление и статус звонка
    IF p_event_type IN ('start', 'dial') THEN
        v_call_direction := 'outgoing';
        v_call_status := 'active';
    ELSIF p_event_type = 'hangup' THEN
        v_call_status := 'completed';
    END IF;

    -- Извлекаем BridgeUniqueid из event_data, если не передан явно
    IF p_bridge_unique_id IS NULL OR p_bridge_unique_id = '' THEN
        p_bridge_unique_id := p_event_data->>'BridgeUniqueid';
        IF p_bridge_unique_id = '' THEN
            p_bridge_unique_id := NULL;
        END IF;
    END IF;

    -- Шаг 1: Ищем "свою" запись по UniqueId
    IF p_unique_id IS NOT NULL AND p_unique_id != '' THEN
        SELECT id INTO v_my_record_id
        FROM call_traces
        WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
        LIMIT 1;
        
        IF v_my_record_id IS NULL THEN
            SELECT id INTO v_my_record_id
            FROM call_traces
            WHERE related_unique_ids @> to_jsonb(p_unique_id)
              AND enterprise_number = p_enterprise_number
            LIMIT 1;
        END IF;
    END IF;

    -- Шаг 2: Ищем запись с BridgeUniqueid
    IF p_bridge_unique_id IS NOT NULL THEN
        SELECT id INTO v_bridge_record_id
        FROM call_traces
        WHERE bridge_unique_id = p_bridge_unique_id AND enterprise_number = p_enterprise_number
        LIMIT 1;
    END IF;

    -- Шаг 3: ОБЪЕДИНЕНИЕ ЗАПИСЕЙ
    IF v_my_record_id IS NOT NULL AND v_bridge_record_id IS NOT NULL 
       AND v_my_record_id != v_bridge_record_id THEN
        
        SELECT 
            call_events, 
            related_unique_ids, 
            phone_number, 
            call_direction,
            telegram_messages,
            http_requests,
            sql_queries,
            integration_responses
        INTO 
            v_my_events, 
            v_my_related_uids, 
            v_my_phone, 
            v_my_direction,
            v_my_telegram,
            v_my_http,
            v_my_sql,
            v_my_integration
        FROM call_traces WHERE id = v_my_record_id;
        
        UPDATE call_traces SET
            call_events = call_events || v_my_events,
            related_unique_ids = related_unique_ids || v_my_related_uids,
            phone_number = COALESCE(phone_number, v_my_phone),
            call_direction = COALESCE(call_direction, v_my_direction),
            telegram_messages = CASE
                WHEN jsonb_typeof(telegram_messages) = 'array' AND jsonb_typeof(v_my_telegram) = 'array'
                THEN telegram_messages || v_my_telegram
                WHEN jsonb_typeof(v_my_telegram) = 'array'
                THEN v_my_telegram
                ELSE telegram_messages
            END,
            http_requests = CASE
                WHEN jsonb_typeof(http_requests) = 'array' AND jsonb_typeof(v_my_http) = 'array'
                THEN http_requests || v_my_http
                WHEN jsonb_typeof(v_my_http) = 'array'
                THEN v_my_http
                ELSE http_requests
            END,
            sql_queries = CASE
                WHEN jsonb_typeof(sql_queries) = 'array' AND jsonb_typeof(v_my_sql) = 'array'
                THEN sql_queries || v_my_sql
                WHEN jsonb_typeof(v_my_sql) = 'array'
                THEN v_my_sql
                ELSE sql_queries
            END,
            integration_responses = CASE
                WHEN jsonb_typeof(integration_responses) = 'array' AND jsonb_typeof(v_my_integration) = 'array'
                THEN integration_responses || v_my_integration
                WHEN jsonb_typeof(v_my_integration) = 'array'
                THEN v_my_integration
                ELSE integration_responses
            END,
            updated_at = NOW()
        WHERE id = v_bridge_record_id;
        
        DELETE FROM call_traces WHERE id = v_my_record_id;
        v_trace_id := v_bridge_record_id;
        
    ELSIF v_bridge_record_id IS NOT NULL THEN
        v_trace_id := v_bridge_record_id;
        
    ELSIF v_my_record_id IS NOT NULL THEN
        IF p_bridge_unique_id IS NOT NULL THEN
            UPDATE call_traces 
            SET bridge_unique_id = p_bridge_unique_id 
            WHERE id = v_my_record_id;
        END IF;
        v_trace_id := v_my_record_id;
    END IF;

    -- ДОБАВЛЕНИЕ СОБЫТИЯ
    IF v_trace_id IS NOT NULL THEN
        -- Запись существует - добавляем событие
        UPDATE call_traces SET
            call_events = call_events || jsonb_build_array(
                jsonb_build_object(
                    'event_sequence', jsonb_array_length(call_events) + 1,
                    'event_type', p_event_type,
                    'event_timestamp', NOW(),
                    'event_data', p_event_data
                )
            ),
            related_unique_ids = CASE 
                WHEN p_unique_id IS NOT NULL AND p_unique_id != '' 
                     AND NOT related_unique_ids @> to_jsonb(p_unique_id)
                THEN related_unique_ids || to_jsonb(p_unique_id)
                ELSE related_unique_ids
            END,
            phone_number = COALESCE(phone_number, p_phone_number),
            call_status = COALESCE(v_call_status, call_status),
            updated_at = NOW()
        WHERE id = v_trace_id;
    ELSE
        -- Записи нет - создаем новую
        -- ✅ ИСПРАВЛЕНИЕ: Добавлен ON CONFLICT DO NOTHING
        INSERT INTO call_traces (
            unique_id, 
            enterprise_number, 
            phone_number, 
            call_direction, 
            call_status, 
            bridge_unique_id,
            related_unique_ids,
            call_events
        ) VALUES (
            COALESCE(p_unique_id, p_bridge_unique_id),
            p_enterprise_number,
            p_phone_number,
            v_call_direction,
            v_call_status,
            p_bridge_unique_id,
            jsonb_build_array(COALESCE(p_unique_id, p_bridge_unique_id)),
            jsonb_build_array(
                jsonb_build_object(
                    'event_sequence', 1,
                    'event_type', p_event_type,
                    'event_timestamp', NOW(),
                    'event_data', p_event_data
                )
            )
        )
        ON CONFLICT (unique_id, enterprise_number) DO NOTHING
        RETURNING id INTO v_trace_id;
        
        -- Если INSERT был проигнорирован из-за конфликта, найдём существующую запись
        IF v_trace_id IS NULL THEN
            SELECT id INTO v_trace_id
            FROM call_traces
            WHERE unique_id = COALESCE(p_unique_id, p_bridge_unique_id)
              AND enterprise_number = p_enterprise_number
            LIMIT 1;
        END IF;
    END IF;

    RETURN v_trace_id;
END;
$function$;

-- ═══════════════════════════════════════════════════════════════════

COMMENT ON FUNCTION public.add_call_event IS 'Исправлено 2025-11-18: Добавлен ON CONFLICT DO NOTHING для обработки дубликатов';

-- ═══════════════════════════════════════════════════════════════════
-- КОНЕЦ ФАЙЛА
-- ═══════════════════════════════════════════════════════════════════


