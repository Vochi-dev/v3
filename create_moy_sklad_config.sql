-- Создание таблицы для конфигурации интеграции с МойСклад
CREATE TABLE IF NOT EXISTS moy_sklad_config (
    id SERIAL PRIMARY KEY,
    enterprise_number VARCHAR(10) NOT NULL UNIQUE,
    config JSONB NOT NULL DEFAULT '{
        "enabled": false,
        "login": "",
        "password": "",
        "api_url": "https://api.moysklad.ru/api/remap/1.2",
        "notifications": {
            "call_notify_mode": "none",
            "notify_incoming": false,
            "notify_outgoing": false
        },
        "incoming_call_actions": {
            "create_order": false,
            "order_status": "Новый",
            "order_source": "Телефонный звонок"
        },
        "outgoing_call_actions": {
            "create_order": false,
            "order_status": "Новый",
            "order_source": "Исходящий звонок"
        }
    }',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Индекс для быстрого поиска по enterprise_number
CREATE INDEX IF NOT EXISTS idx_moy_sklad_config_enterprise ON moy_sklad_config(enterprise_number);

-- Комментарии к таблице
COMMENT ON TABLE moy_sklad_config IS 'Конфигурация интеграции с МойСклад для каждого предприятия';
COMMENT ON COLUMN moy_sklad_config.enterprise_number IS 'Номер предприятия (например, 0367)';
COMMENT ON COLUMN moy_sklad_config.config IS 'JSON конфигурация с настройками интеграции';
COMMENT ON COLUMN moy_sklad_config.created_at IS 'Дата создания записи';
COMMENT ON COLUMN moy_sklad_config.updated_at IS 'Дата последнего обновления';

-- Триггер для автоматического обновления updated_at
CREATE OR REPLACE FUNCTION update_moy_sklad_config_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_moy_sklad_config_updated_at
    BEFORE UPDATE ON moy_sklad_config
    FOR EACH ROW
    EXECUTE FUNCTION update_moy_sklad_config_updated_at();
