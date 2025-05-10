import sqlite3
from datetime import datetime

DB_PATH = "asterisk_events.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                event_type TEXT,
                unique_id TEXT,
                raw_json TEXT
            )
        """)
        conn.commit()

def log_event(event_type, unique_id, raw_json):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO events (timestamp, event_type, unique_id, raw_json)
            VALUES (?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), event_type, unique_id, raw_json))
        conn.commit()
