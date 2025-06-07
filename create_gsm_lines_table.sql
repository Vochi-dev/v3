CREATE TABLE IF NOT EXISTS gsm_lines (
    id SERIAL PRIMARY KEY,
    goip_id INTEGER NOT NULL,
    enterprise_number VARCHAR(50) NOT NULL,
    line_id INTEGER UNIQUE NOT NULL,
    internal_id VARCHAR(20) UNIQUE NOT NULL,
    prefix VARCHAR(10) NOT NULL,
    phone_number VARCHAR(50),
    line_name VARCHAR(255),
    in_schema VARCHAR(255),
    out_schema VARCHAR(255),
    shop VARCHAR(255),
    serial VARCHAR(255),
    slot INTEGER,
    redirect VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_goip
        FOREIGN KEY(goip_id) 
	    REFERENCES goip(id)
	    ON DELETE CASCADE
); 