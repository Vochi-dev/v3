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

# ───────────────── helpers ─────────────────
def require_login(request: Request):
    if request.cookies.get("session") != "valid":
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )

# ───────────────── auth ────────────────────
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401
        )
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie("session", "valid", httponly=True, max_age=86400, path="/admin")
    return resp

# ───────── dashboard ─────────
@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("", include_in_schema=False)          # /admin → /dashboard
async def dashboard_alias(request: Request):
    return await dashboard(request)

# ───────── email-users ───────
@router.get("/email-users", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def email_users_admin(request: Request):
    users = sorted(get_all_emails(), key=lambda x: x["number"])
    return templates.TemplateResponse("email_users.html", {"request": request, "users": users})

@router.post("/upload-emails", dependencies=[Depends(require_login)])
async def upload_emails(file: UploadFile = File(...)):
    raw = await file.read()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    rows = [
        {"email": (r.get("Email") or r.get("email") or "").strip(),
         "name":  (r.get("NAME")  or r.get("Name")  or r.get("name")  or "").strip()}
        for r in reader if (r.get("Email") or r.get("email"))
    ]
    add_or_update_emails_from_file(rows)
    return RedirectResponse("/admin/email-users", status_code=303)
