# app/routers/email_users.py
# -*- coding: utf-8 -*-
from fastapi import (
    APIRouter,
    Request,
    status,
    HTTPException,
    UploadFile,
    File
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import csv
import io

from app.routers.admin import require_login
from app.services.db import get_connection

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    """
    GET /admin/email-users
    Показывает форму загрузки CSV и таблицу без колонки ID.
    """
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            "SELECT email, name, right_all, right_1, right_2 FROM email_users"
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
    file: UploadFile = File(...)
):
    """
    POST /admin/email-users/upload
    Принимает CSV с колонками number,email,name,right_all,right_1,right_2
    и делает upsert в таблицу email_users.
    """
    require_login(request)

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате CSV")

    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    db = await get_connection()
    try:
        for row in reader:
            number    = row.get("number")
            email     = row.get("email", "").strip()
            name      = row.get("name", "").strip()
            right_all = int(row.get("right_all", 0))
            right_1   = int(row.get("right_1", 0))
            right_2   = int(row.get("right_2", 0))

            await db.execute(
                """
                INSERT INTO email_users
                  (number, email, name, right_all, right_1, right_2)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                  number    = excluded.number,
                  name      = excluded.name,
                  right_all = excluded.right_all,
                  right_1   = excluded.right_1,
                  right_2   = excluded.right_2
                """,
                (number, email, name, right_all, right_1, right_2),
            )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )
