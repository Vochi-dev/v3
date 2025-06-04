CREATE TABLE IF NOT EXISTS incoming_sms (
    id SERIAL PRIMARY KEY,
    receive_time TIMESTAMP,
    source_number VARCHAR(50),
    receive_goip VARCHAR(20),
    sms_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Таблица предприятий
CREATE TABLE IF NOT EXISTS enterprises (
    id SERIAL PRIMARY KEY,
    number TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    bot_token TEXT UNIQUE NOT NULL,
    chat_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    secret TEXT NOT NULL,
    host TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    name2 TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Таблица email пользователей
CREATE TABLE IF NOT EXISTS email_users (
    id SERIAL PRIMARY KEY,
    enterprise_id INTEGER NOT NULL,
    email TEXT UNIQUE NOT NULL,
    FOREIGN KEY (enterprise_id) REFERENCES enterprises(id)
);

-- Таблица telegram пользователей
CREATE TABLE IF NOT EXISTS telegram_users (
    id SERIAL PRIMARY KEY,
    tg_id BIGINT UNIQUE,
    enterprise_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    token TEXT,
    verified BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP,
    UNIQUE(email),
    FOREIGN KEY (enterprise_id) REFERENCES enterprises(id)
);

-- Таблица событий
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    token TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_token ON events(token);

-- Таблица email токенов
CREATE TABLE IF NOT EXISTS email_tokens (
    email TEXT PRIMARY KEY,
    tg_id BIGINT NOT NULL,
    bot_token TEXT NOT NULL,
    token TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
); 