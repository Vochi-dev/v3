from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import ADMIN_PASSWORD

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

def require_login(request: Request):
    if request.cookies.get("session") != "valid":
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER,
                            headers={"Location": "/admin/login"})

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401
        )
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(key="session", value="valid", httponly=True, max_age=86400, path="/admin")
    return response

@router.get("/", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


