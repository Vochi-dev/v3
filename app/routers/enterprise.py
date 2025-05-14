# app/routers/enterprise.py
from fastapi import APIRouter, Request, Depends, HTTPException, status
from app.services.db import get_connection
from .admin import require_login

router = APIRouter(prefix="/admin/enterprises")

# ───────── примеры маршрутов ─────────
@router.get("", dependencies=[Depends(require_login)])
async def list_enterprises(request: Request):
    # Изменяем запрос, чтобы получить все поля
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2
            FROM enterprises
        """)
        enterprises = cur.fetchall()

    # Возвращаем шаблон с предприятиями
    return templates.TemplateResponse("enterprises.html", {
        "request": request,
        "enterprises": enterprises
    })
