from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
import sqlite3, uuid
from app.config import DB_PATH

router = APIRouter()

def _db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email(token: str):
    conn = _db_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM telegram_users WHERE token = ?", (token,))
    row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Неверная ссылка")

    # помечаем как подтверждённого
    cur.execute(
        "UPDATE telegram_users SET verified = 1 WHERE token = ?",
        (token,)
    )
    conn.commit()
    conn.close()

    return (
        "<h1>Спасибо!</h1>"
        "<p>Ваш e-mail успешно подтверждён. "
        "Вы можете пользоваться ботом.</p>"
    )
