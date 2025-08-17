-- Настройка интеграции U-ON для тестового предприятия 0367
-- Устанавливаем primary интеграцию на uon и включаем её

UPDATE enterprises 
SET integrations_config = jsonb_set(
    COALESCE(integrations_config::jsonb, '{}'::jsonb),
    '{smart,primary}',
    '"uon"'::jsonb
)
WHERE number = '0367';

UPDATE enterprises 
SET integrations_config = jsonb_set(
    COALESCE(integrations_config::jsonb, '{}'::jsonb),
    '{uon}',
    '{"enabled": true, "api_key": "10IxhY2Py4v6LcUBqU4y1755409304", "log_calls": true}'::jsonb
)
WHERE number = '0367';

-- Проверим результат
SELECT number, integrations_config->'smart'->>'primary' as smart_primary, 
       integrations_config->'uon'->>'enabled' as uon_enabled,
       integrations_config->'uon'->>'api_key' as uon_api_key
FROM enterprises 
WHERE number = '0367';
