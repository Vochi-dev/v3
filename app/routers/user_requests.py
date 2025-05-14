# app/routers/user_requests.py
from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlite3 import connect
from .admin import require_login
from app.config import DB_PATH

router = APIRouter(prefix="/admin/requests")
tpl    = Jinja2Templates(directory="app/templates")

def fetch_all(q:str,p:tuple=()):
    with connect(DB_PATH) as c:
        c.row_factory = lambda cur,row: dict(zip([x[0] for x in cur.description],row))
        return c.execute(q,p).fetchall()

def execute(q:str,p:tuple):
    with connect(DB_PATH) as c:
        c.execute(q,p); c.commit()

# list
@router.get("", response_class=HTMLResponse,
            dependencies=[Depends(require_login)])
async def list_requests(request: Request):
    rows = fetch_all("SELECT * FROM user_requests ORDER BY created_at DESC")
    return tpl.TemplateResponse("user_requests.html", {"request": request, "rows": rows})

# approve-/reject-buttons → POST
@router.post("/{req_id}/{action}", dependencies=[Depends(require_login)])
async def change_status(req_id: int, action: str):
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Bad action")
    new_status = "approved" if action == "approve" else "rejected"
    execute("UPDATE user_requests SET status=? WHERE id=?", (new_status, req_id))
    return RedirectResponse("/admin/requests", status_code=status.HTTP_303_SEE_OTHER)
