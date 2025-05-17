import sqlite3
from typing import List, Dict
from app.config import DB_PATH

def get_all_emails() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM email_users")
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "number": row["number"],
            "email": row["email"],
            "name": row["name"],
            "right_all": bool(row["right_all"]),
            "right_1": bool(row["right_1"]),
            "right_2": bool(row["right_2"]),
        }
        for row in rows
    ]

def add_or_update_emails_from_file(new_entries: List[Dict]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for entry in new_entries:
        email = entry["email"]
        name = entry["name"]

        cur.execute("SELECT COUNT(*) FROM email_users WHERE email = ?", (email,))
        exists = cur.fetchone()[0]

        if exists:
            cur.execute("UPDATE email_users SET name = ? WHERE email = ?", (name, email))
        else:
            # Определяем новый number
            cur.execute("SELECT MAX(number) FROM email_users")
            max_number = cur.fetchone()[0] or 0
            new_number = max_number + 1

            cur.execute("""
                INSERT INTO email_users (number, email, name, right_all, right_1, right_2)
                VALUES (?, ?, ?, 0, 0, 0)
            """, (new_number, email, name))

    conn.commit()
    conn.close()
