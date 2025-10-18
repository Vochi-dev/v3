-- ═══════════════════════════════════════════════════════════════════════════════
-- ИСПРАВЛЕНИЕ ФУНКЦИИ add_call_event V5 (ФИНАЛЬНАЯ)
-- Дата: 2025-10-18
-- Проблема: bridge_create создает запись с BridgeUniqueid, но без UniqueId
--           Потом bridge приходит с обоими - нужно ОБЪЕДИНИТЬ записи
-- Решение: Возвращаем логику объединения + поиск по related_unique_ids
-- ═══════════════════════════════════════════════════════════════════════════════

DROP FUNCTION IF EXISTS add_call_event(VARCHAR, VARCHAR, VARCHAR, JSONB, VARCHAR, VARCHAR);

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
    v_call_direction VARCHAR(10);
    v_call_status VARCHAR(20);
    v_my_record_id BIGINT;
    v_bridge_record_id BIGINT;
    v_my_events JSONB;
    v_my_related_uids JSONB;
    v_my_phone VARCHAR(20);
    v_my_direction VARCHAR(10);
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

    -- ═══════════════════════════════════════════════════════════════════
    -- ЛОГИКА ПОИСКА ЗАПИСЕЙ:
    -- 1. Ищем "свою" запись по UniqueId (в unique_id или related_unique_ids)
    -- 2. Ищем запись с BridgeUniqueid
    -- 3. Если обе найдены и это РАЗНЫЕ записи - ОБЪЕДИНЯЕМ
    -- ═══════════════════════════════════════════════════════════════════

    -- Шаг 1: Ищем "свою" запись по UniqueId
    IF p_unique_id IS NOT NULL AND p_unique_id != '' THEN
        -- Сначала в поле unique_id
        SELECT id INTO v_my_record_id
        FROM call_traces
        WHERE unique_id = p_unique_id AND enterprise_number = p_enterprise_number
        LIMIT 1;
        
        -- Если не нашли, ищем в related_unique_ids
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
        
        -- Переносим данные из "моей" записи в запись с BridgeUniqueid
        SELECT call_events, related_unique_ids, phone_number, call_direction
        INTO v_my_events, v_my_related_uids, v_my_phone, v_my_direction
        FROM call_traces WHERE id = v_my_record_id;
        
        -- Обновляем запись с BridgeUniqueid
        UPDATE call_traces SET
            call_events = call_events || v_my_events,
            related_unique_ids = related_unique_ids || v_my_related_uids,
            phone_number = COALESCE(phone_number, v_my_phone),
            call_direction = COALESCE(call_direction, v_my_direction),
            updated_at = NOW()
        WHERE id = v_bridge_record_id;
        
        -- Удаляем "мою" запись
        DELETE FROM call_traces WHERE id = v_my_record_id;
        
        -- Используем запись с BridgeUniqueid
        v_trace_id := v_bridge_record_id;
        
    ELSIF v_bridge_record_id IS NOT NULL THEN
        -- Есть запись с BridgeUniqueid - используем её
        v_trace_id := v_bridge_record_id;
        
    ELSIF v_my_record_id IS NOT NULL THEN
        -- Есть только "моя" запись - обновляем в ней bridge_unique_id
        IF p_bridge_unique_id IS NOT NULL THEN
            UPDATE call_traces 
            SET bridge_unique_id = p_bridge_unique_id 
            WHERE id = v_my_record_id;
        END IF;
        v_trace_id := v_my_record_id;
    END IF;

    -- ═══════════════════════════════════════════════════════════════════
    -- ДОБАВЛЕНИЕ СОБЫТИЯ
    -- ═══════════════════════════════════════════════════════════════════

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
            -- Добавляем UniqueId в массив, если его там еще нет
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
        INSERT INTO call_traces (
            unique_id, 
            enterprise_number, 
            phone_number, 
            call_direction, 
            call_status, 
            bridge_unique_id,
            related_unique_ids,
            call_events
        )
        VALUES (
            p_unique_id, 
            p_enterprise_number, 
            p_phone_number, 
            v_call_direction, 
            v_call_status,
            p_bridge_unique_id,
            CASE 
                WHEN p_unique_id IS NOT NULL AND p_unique_id != '' 
                THEN jsonb_build_array(p_unique_id)
                ELSE '[]'::jsonb
            END,
            jsonb_build_array(jsonb_build_object(
                'event_sequence', 1,
                'event_type', p_event_type,
                'event_timestamp', NOW(),
                'event_data', p_event_data
            ))
        )
        RETURNING id INTO v_trace_id;
    END IF;

    RETURN v_trace_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION add_call_event IS 'Добавление события в трейс звонка (v5: объединение + поиск по related_unique_ids)';

-- ═══════════════════════════════════════════════════════════════════════════════
-- ГОТОВО!
-- V5 = V3 (объединение) + V4 (поиск по related_unique_ids)
-- ═══════════════════════════════════════════════════════════════════════════════

