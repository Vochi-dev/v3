# app/routers/user_requests.py
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from .admin import require_login          # проверка куки

router    = APIRouter(prefix="/admin/requests")
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
@router.get("",  response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(require_login)])
async def list_requests(request: Request):
    # TODO: достать реальные данные
    return templates.TemplateResponse("requests.html", {"request": request})
