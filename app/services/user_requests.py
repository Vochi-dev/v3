from db import get_db_connection
from datetime import datetime

def create_request(enterprise_id: str, telegram_id: str, username: str):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO enterprise_users (enterprise_id, telegram_id, username, requested_at) VALUES (?, ?, ?, ?)",
        (enterprise_id, telegram_id, username, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def list_requests(status: str = 'pending'):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM enterprise_users WHERE status = ? ORDER BY requested_at",
        (status,)
    ).fetchall()
    conn.close()
    return rows

def approve_request(request_id: int):
    conn = get_db_connection()
    approved_at = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE enterprise_users SET status = 'approved', approved_at = ? WHERE id = ?",
        (approved_at, request_id)
    )
    conn.commit()
    # Получаем данные для уведомления пользователя
    row = conn.execute(
        "SELECT telegram_id, enterprise_id FROM enterprise_users WHERE id = ?",
        (request_id,)
    ).fetchone()
    conn.close()
    return row  # tuple (telegram_id, enterprise_id)

def reject_request(request_id: int):
    conn = get_db_connection()
    conn.execute(
        "UPDATE enterprise_users SET status = 'rejected' WHERE id = ?",
        (request_id,)
    )
    conn.commit()
    conn.close()
