from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.services.user_requests import (
    list_requests,
    approve_request,
    reject_request
)
from app.config import ADMIN_PASSWORD
from fastapi.templating import Jinja2Templates
from fastapi import status

router = APIRouter(prefix="/admin/requests", tags=["requests"])
templates = Jinja2Templates(directory="app/templates")

# Переиспользуем зависимость require_login из admin.py
from app.routers.admin import require_login

# Jinja окружение для ручного рендеринга (если нужно)
env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html", "xml"])
)

@router.get("/", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def requests_list(request: Request):
    items = list_requests('pending')
    return templates.TemplateResponse("requests_list.html", {"request": request, "requests": items})

@router.post("/approve/{request_id}", dependencies=[Depends(require_login)])
async def requests_approve(request_id: int):
    row = approve_request(request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    telegram_id, enterprise_id = row
    # уведомим пользователя ботом
    from bot import bot  # ваш экземпляр Bot
    bot.send_message(
        chat_id=int(telegram_id),
        text=f"Ваша заявка на подключение к предприятию {enterprise_id} одобрена!"
    )
    return RedirectResponse(url="/admin/requests", status_code=303)

@router.post("/reject/{request_id}", dependencies=[Depends(require_login)])
async def requests_reject(request_id: int):
    reject_request(request_id)
    return RedirectResponse(url="/admin/requests", status_code=303)
