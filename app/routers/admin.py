from fastapi import (
    APIRouter, Request, Form, Depends,
    HTTPException, status, UploadFile, File
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import ADMIN_PASSWORD
from app.services.users import get_all_emails, add_or_update_emails_from_file
import csv, io

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

# ───────────────────────── helpers ──────────────────────────
def require_login(request: Request):
    if request.cookies.get("session") != "valid":
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )

# ───────────────────────── auth ─────────────────────────────
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401
        )
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.set_cookie("session", "valid", httponly=True, max_age=86400, path="/admin")
    return resp

# ──────────────────────── dashboard ─────────────────────────
@router.get("/", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# поддерживаем URL с завершающим «/»
@router.get("", include_in_schema=False)          # /admin/
async def dashboard_slash(request: Request):
    return await dashboard(request)

# ───────────────────── email-users page ─────────────────────
@router.get("/email-users", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def email_users_admin(request: Request):
    users = get_all_emails()
    users.sort(key=lambda x: x["number"])
    return templates.TemplateResponse("email_users.html", {"request": request, "users": users})

# ───────────────────── загрузка CSV ─────────────────────────
@router.post("/upload-emails", dependencies=[Depends(require_login)])
async def upload_emails(file: UploadFile = File(...)):
    # читаем файл целиком
    raw_bytes = await file.read()                 # bytes
    text_io = io.StringIO(raw_bytes.decode("utf-8"))

    reader = csv.DictReader(text_io)
    new_entries = []
    for row in reader:
        email = (row.get("Email") or row.get("email") or "").strip()
        name  = (row.get("NAME")  or row.get("Name")  or row.get("name") or "").strip()
        if email:
            new_entries.append({"email": email, "name": name})

    add_or_update_emails_from_file(new_entries)
    return RedirectResponse("/admin/email-users", status_code=303)