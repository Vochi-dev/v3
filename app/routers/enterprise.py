from fastapi import APIRouter, Request, Depends
from app.services.db import get_connection
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/admin/enterprises")

# создаем объект templates, указав директорию с шаблонами
templates = Jinja2Templates(directory="app/templates")

@router.get("")
async def list_enterprises(request: Request):
    # Получаем все данные из базы, включая name2
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2 FROM enterprises")
        rows = cur.fetchall()

    # Рендерим шаблон с полным списком данных
    return templates.TemplateResponse("enterprises.html", {"request": request, "rows": rows})
