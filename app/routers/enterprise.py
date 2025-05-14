from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from .admin import require_login

router    = APIRouter(prefix="/admin/enterprises")
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
@router.get("",  response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def list_enterprises(request: Request):
    # TODO: передать фактические данные
    return templates.TemplateResponse("enterprises.html", {"request": request})
