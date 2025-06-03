-- Создание таблицы enterprises
CREATE TABLE IF NOT EXISTS enterprises (
    id SERIAL PRIMARY KEY,
    number VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    secret VARCHAR(255) NOT NULL,
    bot_token VARCHAR(255),
    chat_id VARCHAR(255),
    ip VARCHAR(50),
    host VARCHAR(255),
    name2 VARCHAR(255),
    CONSTRAINT enterprises_number_unique UNIQUE (number),
    CONSTRAINT enterprises_name2_unique UNIQUE (name2)
);

-- Создание таблицы asterisk_logs
CREATE TABLE IF NOT EXISTS asterisk_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    unique_id VARCHAR(255) NOT NULL,
    token VARCHAR(255) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    raw_data JSONB NOT NULL
);

-- Создание индексов для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_asterisk_logs_token ON asterisk_logs(token);
CREATE INDEX IF NOT EXISTS idx_asterisk_logs_unique_id ON asterisk_logs(unique_id);
CREATE INDEX IF NOT EXISTS idx_asterisk_logs_timestamp ON asterisk_logs(timestamp DESC); 