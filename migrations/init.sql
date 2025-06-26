-- Создание таблицы предприятий
CREATE TABLE IF NOT EXISTS enterprises (
    number VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    bot_token VARCHAR(100),
    active BOOLEAN DEFAULT true,
    chat_id VARCHAR(50),
    ip VARCHAR(50),
    secret VARCHAR(100),
    host VARCHAR(255),
    name2 VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы пользователей email
CREATE TABLE IF NOT EXISTS email_users (
    number VARCHAR(50),
    email VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255),
    right_all INTEGER DEFAULT 0,
    right_1 INTEGER DEFAULT 0,
    right_2 INTEGER DEFAULT 0
);

-- Создание таблицы пользователей telegram
CREATE TABLE IF NOT EXISTS telegram_users (
    tg_id BIGINT PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    bot_token VARCHAR(100)
);

-- Создание таблицы связи пользователей с предприятиями
CREATE TABLE IF NOT EXISTS enterprise_users (
    telegram_id BIGINT,
    enterprise_id VARCHAR(50),
    status VARCHAR(20) DEFAULT 'pending',
    PRIMARY KEY (telegram_id, enterprise_id),
    FOREIGN KEY (telegram_id) REFERENCES telegram_users(tg_id),
    FOREIGN KEY (enterprise_id) REFERENCES enterprises(number)
);

-- Связующая таблица для отношения "многие ко многим" между SIP-линиями и исходящими схемами
CREATE TABLE IF NOT EXISTS sip_outgoing_schema_assignments (
    id SERIAL PRIMARY KEY,
    enterprise_number VARCHAR(255) NOT NULL,
    sip_line_name VARCHAR(255) NOT NULL,
    schema_name VARCHAR(255) NOT NULL,
    UNIQUE (enterprise_number, sip_line_name, schema_name)
); 