-- ═══════════════════════════════════════════════════════════════════
-- ИСПРАВЛЕНИЕ ФУНКЦИЙ LOGGER ДЛЯ ОБРАБОТКИ ДУБЛИКАТОВ (V2)
-- Дата: 2025-11-18
-- Проблема: ON CONFLICT не работает с партиционированными таблицами
-- Решение: Использовать проверку EXISTS перед INSERT
-- ═══════════════════════════════════════════════════════════════════

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
        -- ✅ ИСПРАВЛЕНИЕ V2: Проверяем существование ПЕРЕД INSERT
        -- Это защищает от race condition и дубликатов
        SELECT id INTO v_trace_id
        FROM call_traces
        WHERE unique_id = COALESCE(p_unique_id, p_bridge_unique_id)
          AND enterprise_number = p_enterprise_number
        LIMIT 1;
        
        -- Если запись всё ещё не найдена - создаём
        IF v_trace_id IS NULL THEN
            BEGIN
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
                RETURNING id INTO v_trace_id;
            EXCEPTION
                WHEN unique_violation THEN
                    -- Если всё-таки произошёл дубликат (race condition) - находим запись
                    SELECT id INTO v_trace_id
                    FROM call_traces
                    WHERE unique_id = COALESCE(p_unique_id, p_bridge_unique_id)
                      AND enterprise_number = p_enterprise_number
                    LIMIT 1;
            END;
        END IF;
    END IF;

    RETURN v_trace_id;
END;
$function$;

COMMENT ON FUNCTION public.add_call_event IS 'Исправлено 2025-11-18 V2: Добавлена проверка EXISTS + обработка unique_violation для партиционированных таблиц';

-- ═══════════════════════════════════════════════════════════════════

-- 2. ФУНКЦИЯ: add_http_request
-- Исправление: Добавлена обработка unique_violation в INSERT
CREATE OR REPLACE FUNCTION public.add_http_request(
    p_unique_id character varying, 
    p_enterprise_number character varying, 
    p_method character varying, 
    p_url text, 
    p_request_data jsonb DEFAULT NULL::jsonb, 
    p_response_data jsonb DEFAULT NULL::jsonb, 
    p_status_code integer DEFAULT NULL::integer, 
    p_duration_ms double precision DEFAULT NULL::double precision, 
    p_error text DEFAULT NULL::text
)
RETURNS boolean
LANGUAGE plpgsql
AS $function$
DECLARE
    v_trace_id BIGINT;
    v_current_requests JSONB;
    v_sequence INTEGER;
BEGIN
    -- Пытаемся найти существующую запись по unique_id или related_unique_ids
    SELECT id, http_requests INTO v_trace_id, v_current_requests
    FROM call_traces
    WHERE enterprise_number = p_enterprise_number
      AND (
          unique_id = p_unique_id
          OR related_unique_ids @> jsonb_build_array(p_unique_id)
      )
    LIMIT 1;

    IF v_trace_id IS NOT NULL THEN
        -- Проверяем тип http_requests и определяем sequence
        IF v_current_requests IS NULL OR jsonb_typeof(v_current_requests) != 'array' THEN
            v_sequence := 1;
            v_current_requests := '[]'::jsonb;
        ELSE
            v_sequence := jsonb_array_length(v_current_requests) + 1;
        END IF;

        -- Добавляем HTTP запрос к существующей записи
        UPDATE call_traces SET
            http_requests = v_current_requests || jsonb_build_array(jsonb_build_object(
                'sequence', v_sequence,
                'method', p_method,
                'url', p_url,
                'request_data', p_request_data,
                'response_data', p_response_data,
                'status_code', p_status_code,
                'duration_ms', p_duration_ms,
                'error', p_error,
                'timestamp', NOW()
            )),
            updated_at = NOW()
        WHERE id = v_trace_id;

        RETURN TRUE;
    ELSE
        -- ✅ ИСПРАВЛЕНИЕ: Проверяем существование ПЕРЕД INSERT
        SELECT id INTO v_trace_id
        FROM call_traces
        WHERE unique_id = p_unique_id
          AND enterprise_number = p_enterprise_number
        LIMIT 1;
        
        -- Если запись всё ещё не найдена - создаём
        IF v_trace_id IS NULL THEN
            BEGIN
                INSERT INTO call_traces (unique_id, enterprise_number, http_requests, call_status)
                VALUES (p_unique_id, p_enterprise_number,
                        jsonb_build_array(jsonb_build_object(
                            'sequence', 1,
                            'method', p_method,
                            'url', p_url,
                            'request_data', p_request_data,
                            'response_data', p_response_data,
                            'status_code', p_status_code,
                            'duration_ms', p_duration_ms,
                            'error', p_error,
                            'timestamp', NOW()
                        )),
                        'active'
                ) RETURNING id INTO v_trace_id;
            EXCEPTION
                WHEN unique_violation THEN
                    -- Если всё-таки произошёл дубликат (race condition) - находим запись и добавляем HTTP
                    SELECT id, http_requests INTO v_trace_id, v_current_requests
                    FROM call_traces
                    WHERE unique_id = p_unique_id
                      AND enterprise_number = p_enterprise_number
                    LIMIT 1;
                    
                    IF v_trace_id IS NOT NULL THEN
                        IF v_current_requests IS NULL OR jsonb_typeof(v_current_requests) != 'array' THEN
                            v_sequence := 1;
                            v_current_requests := '[]'::jsonb;
                        ELSE
                            v_sequence := jsonb_array_length(v_current_requests) + 1;
                        END IF;
                        
                        UPDATE call_traces SET
                            http_requests = v_current_requests || jsonb_build_array(jsonb_build_object(
                                'sequence', v_sequence,
                                'method', p_method,
                                'url', p_url,
                                'request_data', p_request_data,
                                'response_data', p_response_data,
                                'status_code', p_status_code,
                                'duration_ms', p_duration_ms,
                                'error', p_error,
                                'timestamp', NOW()
                            )),
                            updated_at = NOW()
                        WHERE id = v_trace_id;
                    END IF;
            END;
        END IF;

        RETURN TRUE;
    END IF;
END;
$function$;

COMMENT ON FUNCTION public.add_http_request(character varying, character varying, character varying, text, jsonb, jsonb, integer, double precision, text) IS 'Исправлено 2025-11-18: Добавлена проверка EXISTS + обработка unique_violation';

-- ═══════════════════════════════════════════════════════════════════

-- 3. ФУНКЦИЯ: add_sql_query
-- Исправление: Добавлена обработка unique_violation в INSERT
CREATE OR REPLACE FUNCTION public.add_sql_query(
    p_unique_id character varying, 
    p_enterprise_number character varying, 
    p_query text, 
    p_parameters jsonb DEFAULT NULL::jsonb, 
    p_result jsonb DEFAULT NULL::jsonb, 
    p_duration_ms double precision DEFAULT NULL::double precision, 
    p_error text DEFAULT NULL::text
)
RETURNS boolean
LANGUAGE plpgsql
AS $function$
DECLARE
    v_trace_id BIGINT;
BEGIN
    -- Пытаемся найти существующую запись
    SELECT id INTO v_trace_id
    FROM call_traces
    WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
    LIMIT 1;

    IF v_trace_id IS NOT NULL THEN
        -- Добавляем SQL запрос к существующей записи
        UPDATE call_traces SET
            sql_queries = sql_queries || jsonb_build_array(jsonb_build_object(
                'sequence', jsonb_array_length(sql_queries) + 1,
                'query', p_query,
                'parameters', p_parameters,
                'result', p_result,
                'duration_ms', p_duration_ms,
                'error', p_error,
                'timestamp', NOW()
            )),
            updated_at = NOW()
        WHERE id = v_trace_id;

        RETURN TRUE;
    ELSE
        -- ✅ ИСПРАВЛЕНИЕ: Обработка unique_violation
        BEGIN
            INSERT INTO call_traces (unique_id, enterprise_number, sql_queries, call_status)
            VALUES (p_unique_id, p_enterprise_number,
                    jsonb_build_array(jsonb_build_object(
                        'sequence', 1,
                        'query', p_query,
                        'parameters', p_parameters,
                        'result', p_result,
                        'duration_ms', p_duration_ms,
                        'error', p_error,
                        'timestamp', NOW()
                    )),
                    'active'
            ) RETURNING id INTO v_trace_id;
        EXCEPTION
            WHEN unique_violation THEN
                -- Race condition - находим запись и добавляем SQL
                SELECT id INTO v_trace_id
                FROM call_traces
                WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
                LIMIT 1;
                
                IF v_trace_id IS NOT NULL THEN
                    UPDATE call_traces SET
                        sql_queries = sql_queries || jsonb_build_array(jsonb_build_object(
                            'sequence', jsonb_array_length(sql_queries) + 1,
                            'query', p_query,
                            'parameters', p_parameters,
                            'result', p_result,
                            'duration_ms', p_duration_ms,
                            'error', p_error,
                            'timestamp', NOW()
                        )),
                        updated_at = NOW()
                    WHERE id = v_trace_id;
                END IF;
        END;

        RETURN TRUE;
    END IF;
END;
$function$;

COMMENT ON FUNCTION public.add_sql_query(character varying, character varying, text, jsonb, jsonb, double precision, text) IS 'Исправлено 2025-11-18: Добавлена обработка unique_violation';

-- ═══════════════════════════════════════════════════════════════════

-- 4. ФУНКЦИЯ: add_telegram_message
-- Исправление: Добавлена обработка unique_violation в INSERT
CREATE OR REPLACE FUNCTION public.add_telegram_message(
    p_unique_id character varying, 
    p_enterprise_number character varying, 
    p_chat_id bigint, 
    p_message_type character varying, 
    p_action character varying, 
    p_message_id integer DEFAULT NULL::integer, 
    p_message_text text DEFAULT NULL::text, 
    p_error text DEFAULT NULL::text
)
RETURNS boolean
LANGUAGE plpgsql
AS $function$
DECLARE
    v_trace_id BIGINT;
BEGIN
    -- Пытаемся найти существующую запись
    SELECT id INTO v_trace_id
    FROM call_traces
    WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
    LIMIT 1;

    IF v_trace_id IS NOT NULL THEN
        -- Добавляем Telegram сообщение к существующей записи
        UPDATE call_traces SET
            telegram_messages = COALESCE(telegram_messages, '[]'::jsonb) || jsonb_build_array(jsonb_build_object(
                'sequence', COALESCE(jsonb_array_length(telegram_messages), 0) + 1,
                'chat_id', p_chat_id,
                'message_type', p_message_type,
                'action', p_action,
                'message_id', p_message_id,
                'message_text', p_message_text,
                'error', p_error,
                'timestamp', NOW()
            )),
            updated_at = NOW()
        WHERE id = v_trace_id;

        RETURN TRUE;
    ELSE
        -- ✅ ИСПРАВЛЕНИЕ: Обработка unique_violation
        BEGIN
            INSERT INTO call_traces (unique_id, enterprise_number, telegram_messages, call_status)
            VALUES (p_unique_id, p_enterprise_number,
                    jsonb_build_array(jsonb_build_object(
                        'sequence', 1,
                        'chat_id', p_chat_id,
                        'message_type', p_message_type,
                        'action', p_action,
                        'message_id', p_message_id,
                        'message_text', p_message_text,
                        'error', p_error,
                        'timestamp', NOW()
                    )),
                    'active'
            ) RETURNING id INTO v_trace_id;
        EXCEPTION
            WHEN unique_violation THEN
                -- Race condition - находим запись и добавляем сообщение
                SELECT id INTO v_trace_id
                FROM call_traces
                WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
                LIMIT 1;
                
                IF v_trace_id IS NOT NULL THEN
                    UPDATE call_traces SET
                        telegram_messages = COALESCE(telegram_messages, '[]'::jsonb) || jsonb_build_array(jsonb_build_object(
                            'sequence', COALESCE(jsonb_array_length(telegram_messages), 0) + 1,
                            'chat_id', p_chat_id,
                            'message_type', p_message_type,
                            'action', p_action,
                            'message_id', p_message_id,
                            'message_text', p_message_text,
                            'error', p_error,
                            'timestamp', NOW()
                        )),
                        updated_at = NOW()
                    WHERE id = v_trace_id;
                END IF;
        END;

        RETURN TRUE;
    END IF;
END;
$function$;

COMMENT ON FUNCTION public.add_telegram_message(character varying, character varying, bigint, character varying, character varying, integer, text, text) IS 'Исправлено 2025-11-18: Добавлена обработка unique_violation';

-- ═══════════════════════════════════════════════════════════════════

-- 5. ФУНКЦИЯ: add_integration_response
-- Исправление: Добавлена обработка unique_violation в INSERT
CREATE OR REPLACE FUNCTION public.add_integration_response(
    p_unique_id character varying, 
    p_enterprise_number character varying, 
    p_integration character varying, 
    p_endpoint text, 
    p_request_data jsonb DEFAULT NULL::jsonb, 
    p_response_data jsonb DEFAULT NULL::jsonb, 
    p_status_code integer DEFAULT NULL::integer, 
    p_duration_ms double precision DEFAULT NULL::double precision, 
    p_error text DEFAULT NULL::text
)
RETURNS boolean
LANGUAGE plpgsql
AS $function$
DECLARE
    v_trace_id BIGINT;
BEGIN
    -- Пытаемся найти существующую запись
    SELECT id INTO v_trace_id
    FROM call_traces
    WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
    LIMIT 1;

    IF v_trace_id IS NOT NULL THEN
        -- Добавляем integration response к существующей записи
        UPDATE call_traces SET
            integration_responses = integration_responses || jsonb_build_array(jsonb_build_object(
                'sequence', jsonb_array_length(integration_responses) + 1,
                'integration', p_integration,
                'endpoint', p_endpoint,
                'request_data', p_request_data,
                'response_data', p_response_data,
                'status_code', p_status_code,
                'duration_ms', p_duration_ms,
                'error', p_error,
                'timestamp', NOW()
            )),
            updated_at = NOW()
        WHERE id = v_trace_id;

        RETURN TRUE;
    ELSE
        -- ✅ ИСПРАВЛЕНИЕ: Обработка unique_violation
        BEGIN
            INSERT INTO call_traces (unique_id, enterprise_number, integration_responses, call_status)
            VALUES (p_unique_id, p_enterprise_number,
                    jsonb_build_array(jsonb_build_object(
                        'sequence', 1,
                        'integration', p_integration,
                        'endpoint', p_endpoint,
                        'request_data', p_request_data,
                        'response_data', p_response_data,
                        'status_code', p_status_code,
                        'duration_ms', p_duration_ms,
                        'error', p_error,
                        'timestamp', NOW()
                    )),
                    'active'
            ) RETURNING id INTO v_trace_id;
        EXCEPTION
            WHEN unique_violation THEN
                -- Race condition - находим запись и добавляем integration
                SELECT id INTO v_trace_id
                FROM call_traces
                WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
                LIMIT 1;
                
                IF v_trace_id IS NOT NULL THEN
                    UPDATE call_traces SET
                        integration_responses = integration_responses || jsonb_build_array(jsonb_build_object(
                            'sequence', jsonb_array_length(integration_responses) + 1,
                            'integration', p_integration,
                            'endpoint', p_endpoint,
                            'request_data', p_request_data,
                            'response_data', p_response_data,
                            'status_code', p_status_code,
                            'duration_ms', p_duration_ms,
                            'error', p_error,
                            'timestamp', NOW()
                        )),
                        updated_at = NOW()
                    WHERE id = v_trace_id;
                END IF;
        END;

        RETURN TRUE;
    END IF;
END;
$function$;

COMMENT ON FUNCTION public.add_integration_response(character varying, character varying, character varying, text, jsonb, jsonb, integer, double precision, text) IS 'Исправлено 2025-11-18: Добавлена обработка unique_violation';

-- ═══════════════════════════════════════════════════════════════════
-- КОНЕЦ ФАЙЛА - ВСЕ 5 ФУНКЦИЙ ИСПРАВЛЕНЫ
-- ═══════════════════════════════════════════════════════════════════

