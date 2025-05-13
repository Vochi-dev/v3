import sqlite3
import logging
from config import DB_PATH

def get_db_connection():
    """
    Returns a connection to SQLite database
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Initialize database tables by delegating to services.events.init_database_tables.
    """
    from services.events import init_database_tables
    init_database_tables()
