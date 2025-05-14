from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
import sqlite3
from app.config import DB_PATH

router = APIRouter()

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(token: str):
    with _conn() as conn:
        cur = conn.execute(
            "SELECT tg_id, email, verified FROM telegram_users WHERE token = ?",
            (token,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Bad token")

        if row["verified"]:
            return "<h2>Ваш e-mail уже подтверждён 👍</h2>"

        conn.execute("UPDATE telegram_users SET verified = 1 WHERE token = ?", (token,))
        conn.commit()

    return "<h2>Спасибо! E-mail подтверждён. Вернитесь в Telegram — бот уже ждёт 😉</h2>"