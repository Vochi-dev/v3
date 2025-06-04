import psycopg2
from psycopg2.extras import DictCursor

DB_CONFIG = {
    'dbname': 'postgres',
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'host': 'localhost',
    'port': '5432'
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def create_tables():
    with open('create_tables.sql', 'r') as f:
        sql = f.read()
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()

def save_sms(receive_time, source_number, receive_goip, sms_text):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incoming_sms (receive_time, source_number, receive_goip, sms_text)
                VALUES (%s, %s, %s, %s)
                """,
                (receive_time, source_number, receive_goip, sms_text)
            )
        conn.commit()
    finally:
        conn.close()

def get_last_sms(limit=50):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM incoming_sms 
                ORDER BY receive_time DESC 
                LIMIT %s
                """,
                (limit,)
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close() 