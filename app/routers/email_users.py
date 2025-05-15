# app/routers/email_users.py
# -*- coding: utf-8 -*-
import csv
import io
import base64

from fastapi import (
    APIRouter, Request, status, HTTPException,
    UploadFile, File, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import aiosqlite

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.db import get_connection
from app.services.user_sync import sync_users_from_csv

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
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
              tu.tg_id           AS tg_id,
              ent.name           AS enterprise_name
            FROM email_users eu
            LEFT JOIN telegram_users tu
              ON eu.email = tu.email
            LEFT JOIN enterprise_users uut
              ON uut.telegram_id = tu.tg_id
            LEFT JOIN enterprises ent
              ON uut.enterprise_id = ent.number
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


@router.post("/upload", response_class=HTMLResponse)
async def upload_email_users(
    request: Request,
    file: UploadFile = File(...)
):
    """
    Первый шаг: читаем CSV, обновляем справочник email_users,
    вычисляем список to_remove и показываем страницу подтверждения.
    """
    require_login(request)

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате CSV")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    # 1) Обновляем справочник email_users
    db = await get_connection()
    try:
        for row in reader:
            number    = row.get("number")
            email     = row.get("email", "").strip().lower()
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

    # 2) вычисляем to_remove без фактического удаления
    #    sync_users_from_csv умеет удалять — мы повторим логику тут, но без удаления
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    # собираем mapping number->emails из CSV
    csv_map = {}
    for row in reader:
        num = (row.get("number") or "").strip()
        em  = (row.get("email")  or "").strip().lower()
        if num and em:
            csv_map.setdefault(num, set()).add(em)

    to_remove = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # пробег по enterprise_users
        async with db.execute(
            """
            SELECT u.telegram_id AS tg_id,
                   tu.email,
                   u.enterprise_id AS number
            FROM enterprise_users u
            JOIN telegram_users tu ON u.telegram_id = tu.tg_id
            """
        ) as cur:
            for r in await cur.fetchall():
                num   = r["number"]
                email = r["email"].lower()
                if email not in csv_map.get(num, set()):
                    to_remove.append(dict(tg_id=r["tg_id"], email=email, number=num))

    # 3) Рендерим страницу подтверждения
    # кодируем CSV в base64, чтобы вернуть дальше
    b64 = base64.b64encode(content).decode()
    return templates.TemplateResponse(
        "confirm_sync.html",
        {
            "request": request,
            "to_remove": to_remove,
            "csv": b64,
        }
    )


@router.post("/upload/confirm", response_class=RedirectResponse)
async def confirm_upload(
    request: Request,
    csv: str = Form(...),
    confirm: str = Form(...)
):
    require_login(request)

    # если админ отменил — сразу редирект
    if confirm != "yes":
        return RedirectResponse(url="/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    # иначе раскодируем CSV и запускаем финальный sync
    content = base64.b64decode(csv.encode())
    await sync_users_from_csv(content)

    return RedirectResponse(url="/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    """ Удаление через кнопку «Delete» в реестре. """
    require_login(request)

    # ... (всё как было ранее) ...
    # убираем для краткости, оставьте ваш существующий код
