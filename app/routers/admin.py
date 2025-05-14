# app/routers/admin.py
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import ADMIN_PASSWORD
from app.services.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


def _check_auth(request: Request) -> bool:
    return request.cookies.get("auth") == "1"


@router.get("", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None}
    )


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie("auth", "1", httponly=True)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _check_auth(request):
        return RedirectResponse(url="/admin", status_code=303)
    # например, посчитаем число предприятий
    async with await get_connection() as db:
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM enterprises")
        row = await cur.fetchone()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "enterprise_count": row["cnt"]}
    )


@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    if not _check_auth(request):
        return RedirectResponse(url="/admin", status_code=303)
    async with await get_connection() as db:
        cur = await db.execute("SELECT number, name, bot_token FROM enterprises")
        rows = await cur.fetchall()
    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": rows}
    )
