# app/routers/user_requests.py
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.routers.admin import require_login
from app.services.db import get_connection

router = APIRouter(prefix="/admin/requests", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_requests(request: Request):
    """
    GET /admin/requests → список заявок на подключение
    """
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            """
            SELECT id, created_at, email, enterprise, comment, status
            FROM user_requests
            ORDER BY created_at DESC
            """
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "user_requests.html",
        {"request": request, "requests": rows}
    )


@router.post("/approve/{request_id}", response_class=RedirectResponse)
async def approve_request(request_id: int, request: Request):
    """
    POST /admin/requests/approve/{request_id} → одобрить заявку
    """
    require_login(request)
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE user_requests SET status='approved' WHERE id = ?",
            (request_id,),
        )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(url="/admin/requests", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reject/{request_id}", response_class=RedirectResponse)
async def reject_request(request_id: int, request: Request):
    """
    POST /admin/requests/reject/{request_id} → отклонить заявку
    """
    require_login(request)
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE user_requests SET status='rejected' WHERE id = ?",
            (request_id,),
        )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(url="/admin/requests", status_code=status.HTTP_303_SEE_OTHER)
