from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from .admin import require_login
from app.services.admin_tables import get_enterprises

router    = APIRouter(prefix="/admin/enterprises")
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
@router.get("",  response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(require_login)])
async def list_enterprises(request: Request):
    enterprises = get_enterprises()
    return templates.TemplateResponse("enterprises.html",
                                      {"request": request, "enterprises": enterprises})
