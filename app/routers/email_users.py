# app/routers/email_users.py
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.routers.admin import require_login
from app.services.db import get_connection

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            "SELECT number, email, name, right_all, right_1, right_2 FROM email_users"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows}
    )
