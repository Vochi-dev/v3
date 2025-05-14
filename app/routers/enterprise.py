# app/routers/enterprise.py
import sqlite3, secrets, datetime
from fastapi import APIRouter, Request, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import DB_PATH
from .admin import require_login          # проверка cookie-сессии

router    = APIRouter(prefix="/admin/enterprises")
templates = Jinja2Templates(directory="app/templates")

# ───────────────────── helpers ──────────────────────
def fetch_all(q: str, p: tuple = ()):
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        return c.execute(q, p).fetchall()

def exec_sql(q: str, p: tuple):
    with sqlite3.connect(DB_PATH) as c:
        c.execute(q, p)
        c.commit()

# ───────────────────── list page ─────────────────────
@router.get("", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def list_enterprises(request: Request):
    rows = fetch_all("SELECT * FROM enterprises ORDER BY number")
    return templates.TemplateResponse("enterprises.html",
        {"request": request, "rows": rows})

# поддерживаем URL «/admin/enterprises/» (со slash)
@router.get("/", include_in_schema=False)
async def list_enterprises_slash(request: Request):
    return await list_enterprises(request)

# ───────────────────── add-form ─────────────────────
@router.get("/new", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def new_enterprise_form(request: Request):
    return templates.TemplateResponse("enterprise_form.html", {"request": request})

@router.post("/new", dependencies=[Depends(require_login)])
async def create_enterprise(
        number:    str = Form(...),
        name:      str = Form(...),
        bot_token: str = Form(...),
        chat_id:   str = Form(...),
        ip:        str = Form(...),
        host:      str = Form(...)
    ):
    secret = secrets.token_hex(16)
    created_at = datetime.datetime.utcnow().isoformat()

    try:
        exec_sql("""
            INSERT INTO enterprises
              (number, name, bot_token, chat_id, ip, secret, host, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (number, name, bot_token, chat_id, ip, secret, host, created_at))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Такой номер уже существует")

    return RedirectResponse("/admin/enterprises", status_code=303)
