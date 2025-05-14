from fastapi import APIRouter, Request, Depends
from app.services.db import get_connection
from fastapi.templating import Jinja2Templates  # импортируем Jinja2Templates

router = APIRouter(prefix="/admin/enterprises")

# создаем объект templates, указав директорию с шаблонами
templates = Jinja2Templates(directory="app/templates")

@router.get("")
async def list_enterprises(request: Request):
    # Получаем данные из базы
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM enterprises")
        rows = cur.fetchall()

    # Рендерим шаблон с данными
    return templates.TemplateResponse("enterprises.html", {"request": request, "rows": rows})
