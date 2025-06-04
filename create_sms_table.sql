CREATE TABLE IF NOT EXISTS incoming_sms (
    id SERIAL PRIMARY KEY,
    receive_time TIMESTAMP NOT NULL,
    source_number VARCHAR(50) NOT NULL,
    receive_goip VARCHAR(20) NOT NULL,
    sms_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
); 