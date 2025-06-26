-- Таблица для связи личных номеров пользователей с входящими схемами (многие ко многим)
CREATE TABLE IF NOT EXISTS user_personal_phone_incoming_assignments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    schema_id VARCHAR(255) NOT NULL,
    schema_name VARCHAR(255) NOT NULL,
    enterprise_number VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_user
        FOREIGN KEY(user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_dial_schema
        FOREIGN KEY(schema_id)
        REFERENCES dial_schemas(schema_id)
        ON DELETE CASCADE,
    UNIQUE (user_id, schema_id)
);

-- Таблица для связи внутренних номеров с входящими схемами (многие ко многим)
CREATE TABLE IF NOT EXISTS user_internal_phone_incoming_assignments (
    id SERIAL PRIMARY KEY,
    internal_phone_id INTEGER NOT NULL,
    schema_id VARCHAR(255) NOT NULL,
    schema_name VARCHAR(255) NOT NULL,
    enterprise_number VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_internal_phone
        FOREIGN KEY(internal_phone_id)
        REFERENCES user_internal_phones(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_dial_schema
        FOREIGN KEY(schema_id)
        REFERENCES dial_schemas(schema_id)
        ON DELETE CASCADE,
    UNIQUE (internal_phone_id, schema_id)
);

-- Индексы для ускорения выборок
CREATE INDEX IF NOT EXISTS idx_user_personal_phone_user_id ON user_personal_phone_incoming_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_user_internal_phone_phone_id ON user_internal_phone_incoming_assignments(internal_phone_id); 