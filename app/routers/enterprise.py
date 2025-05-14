# app/routers/enterprise.py
from fastapi import APIRouter, Request, Depends, Form, status, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlite3 import connect
from .admin import require_login
from app.config import DB_PATH          # путь к БД

router   = APIRouter(prefix="/admin/enterprises")
tpl      = Jinja2Templates(directory="app/templates")

# ───────────────── helpers ─────────────────
def fetch_all(query: str, params: tuple = ()):
    with connect(DB_PATH) as c:
        c.row_factory = lambda cur, row: dict(zip([x[0] for x in cur.description], row))
        return c.execute(query, params).fetchall()

def execute(query: str, params: tuple):
    with connect(DB_PATH) as c:
        c.execute(query, params)
        c.commit()

# ───────────────── list ────────────────────
@router.get("", response_class=HTMLResponse,
            dependencies=[Depends(require_login)])
async def list_enterprises(request: Request):
    rows = fetch_all("SELECT number, name, bot_token, created_at FROM enterprises ORDER BY number")
    return tpl.TemplateResponse("enterprises.html", {"request": request, "rows": rows})

# ───────────────── form (GET) ──────────────
@router.get("/new", response_class=HTMLResponse,
            dependencies=[Depends(require_login)])
async def new_enterprise_form(request: Request):
    return tpl.TemplateResponse("enterprise_form.html", {"request": request})

# ───────────────── form (POST) ─────────────
@router.post("/new", dependencies=[Depends(require_login)])
async def create_enterprise(
        number:     str = Form(...),
        name:       str = Form(...),
        bot_token:  str = Form(...),
        chat_id:    str = Form(...),
        ip:         str = Form(...),
        secret:     str = Form(...),
        host:       str = Form(...)
):
    execute(
        """INSERT INTO enterprises
           (number,name,bot_token,chat_id,ip,secret,host,created_at)
           VALUES (?,?,?,?,?,?,?,datetime('now'))""",
        (number, name, bot_token, chat_id, ip, secret, host)
    )
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)
