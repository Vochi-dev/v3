-- Создаем таблицу предприятий
CREATE TABLE IF NOT EXISTS enterprises (
    number TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bot_token TEXT,
    chat_id TEXT,
    ip TEXT,
    secret TEXT,
    host TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    name2 TEXT,
    active INTEGER DEFAULT 1
);

-- Создаем таблицу для telegram пользователей
CREATE TABLE IF NOT EXISTS telegram_users (
    id SERIAL PRIMARY KEY,
    tg_id TEXT NOT NULL,
    bot_token TEXT NOT NULL,
    verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создаем таблицу для email пользователей
CREATE TABLE IF NOT EXISTS email_users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    bot_token TEXT NOT NULL,
    verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создаем таблицу для сообщений
CREATE TABLE IF NOT EXISTS telegram_messages (
    id SERIAL PRIMARY KEY,
    message_id INTEGER,
    event_type TEXT,
    token TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    chat_id TEXT,
    text TEXT
); 