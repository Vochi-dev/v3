import logging
import sqlite3
from contextlib import contextmanager
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DATABASE_FILE = "/root/asterisk-webhook/asterisk_events.db"

@contextmanager
def connect() -> Callable:
    conn = sqlite3.connect(DATABASE_FILE)
    try:
        yield conn
    finally:
        conn.close()

def get_enterprises_with_tokens() -> List[Tuple[str, str, str, str]]:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT number, name, bot_token, chat_id
              FROM enterprises
             WHERE LENGTH(bot_token) > 0
        """)
        return cursor.fetchall()
