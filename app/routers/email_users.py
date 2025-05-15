# app/routers/email_users.py
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.routers.admin import require_login
from app.services.db import get_connection

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    """
    GET /admin/email-users
    Показывает форму загрузки CSV и таблицу с пользователями:
    ID (tg_id), e-mail, имя, права, Unit (предприятие), кнопка Delete.
    """
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            """
            SELECT
              eu.email,
              eu.name,
              eu.right_all,
              eu.right_1,
              eu.right_2,
              tu.tg_id,
              ent.name AS enterprise_name
            FROM email_users eu
            LEFT JOIN telegram_users tu ON eu.email = tu.email
            LEFT JOIN enterprise_users uut ON tu.tg_id = uut.tg_id
            LEFT JOIN enterprises ent ON uut.number = ent.number
            ORDER BY eu.email
            """
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows}
    )


@router.post("/upload", response_class=RedirectResponse)
async def upload_email_users(
    request: Request,
    file: bytes = None  # здесь ваша прежняя реализация
):
    # Ваша существующая логика CSV-загрузки...
    # ...
    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    """
    POST /admin/email-users/delete/{tg_id}
    Удаляет пользователя из telegram_users и enterprise_users.
    """
    require_login(request)
    db = await get_connection()
    try:
        await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db.execute("DELETE FROM enterprise_users WHERE tg_id = ?", (tg_id,))
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )
