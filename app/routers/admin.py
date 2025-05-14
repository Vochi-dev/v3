from fastapi import APIRouter, Request, Form, Depends, HTTPException, status, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import ADMIN_PASSWORD
from app.services.users import get_all_emails, add_or_update_emails_from_file
from io import TextIOWrapper
import csv

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

# Проверка авторизации
def require_login(request: Request):
    if request.cookies.get("session") != "valid":
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )

# Форма логина
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

# Отправка формы логина
@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401
        )
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(key="session", value="valid", httponly=True, max_age=86400, path="/admin")
    return response

# Главная страница админки
@router.get("/", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# Таблица пользователей
@router.get("/email-users", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def email_users_admin(request: Request):
    users = get_all_emails()
    users.sort(key=lambda x: x["number"])
    return templates.TemplateResponse("email_users.html", {"request": request, "users": users})

# Обработка загрузки файла с email'ами
@router.post("/upload-emails", dependencies=[Depends(require_login)])
async def upload_emails(file: UploadFile):
    text_wrapper = TextIOWrapper(file.file, encoding='utf-8')
    reader = csv.DictReader(text_wrapper)
    new_entries = []
    for row in reader:
        email = row.get("Email") or row.get("email") or ""
        name = row.get("NAME") or row.get("Name") or row.get("name") or ""
        if email:
            new_entries.append({"email": email.strip(), "name": name.strip()})
    add_or_update_emails_from_file(new_entries)
    return RedirectResponse(url="/admin/email-users", status_code=303)
