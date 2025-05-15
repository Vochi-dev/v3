# app/routers/email_users.py
# -*- coding: utf-8 -*-
import csv
import io
import base64
from typing import List, Dict

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
    require_login(request)

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате CSV")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    # 1) Новый набор e-mail из CSV
    new_emails = {row.get("email", "").strip().lower() for row in reader if row.get("email")}
    if not new_emails:
        raise HTTPException(status_code=400, detail="В файле нет ни одного e-mail")

    # 2) Обновляем/добавляем записи в email_users
    db = await get_connection()
    try:
        for row in csv.DictReader(io.StringIO(text)):
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

        # 3) Собираем все e-mail из базы после обновления
        cur = await db.execute("SELECT email FROM email_users")
        all_emails = {row[0] for row in await cur.fetchall()}
    finally:
        await db.close()

    # 4) Определяем e-mail, которых нет в новом файле
    to_remove_emails = all_emails - new_emails
    if not to_remove_emails:
        # ничего удалять не нужно — сразу синхроним ботов и возвращаемся
        await sync_users_from_csv(content)
        return RedirectResponse(
            url="/admin/email-users",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # 5) Для каждого e-mail из to_remove_emails получаем tg_id и enterprise_name
    remove_list: List[Dict] = []
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in to_remove_emails)
        query = f"""
            SELECT
              tu.tg_id           AS tg_id,
              eu.email           AS email,
              ent.name           AS enterprise_name
            FROM email_users eu
            LEFT JOIN telegram_users tu
              ON eu.email = tu.email
            LEFT JOIN enterprise_users uut
              ON uut.telegram_id = tu.tg_id
            LEFT JOIN enterprises ent
              ON uut.enterprise_id = ent.number
            WHERE eu.email IN ({placeholders})
        """
        cur = await db2.execute(query, tuple(to_remove_emails))
        rows = await cur.fetchall()
        for row in rows:
            remove_list.append({
                "tg_id": row["tg_id"],
                "email": row["email"],
                "enterprise_name": row["enterprise_name"] or ""
            })

    # 6) Кодируем оригинальный CSV для передачи скрытым полем
    b64 = base64.b64encode(content).decode()

    # 7) Рендерим шаблон подтверждения
    return templates.TemplateResponse(
        "confirm_sync.html",
        {
            "request":   request,
            "to_remove": remove_list,
            "csv_b64":   b64,
        }
    )


@router.post("/upload/confirm", response_class=RedirectResponse)
async def confirm_upload(
    request: Request,
    csv_b64: str  = Form(...),
    confirm: str = Form(...)
):
    require_login(request)

    # отмена админом
    if confirm != "yes":
        return RedirectResponse(
            url="/admin/email-users",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # раскодируем CSV и удаляем из email_users e-mail вне файла
    content = base64.b64decode(csv_b64.encode())
    text    = content.decode("utf-8-sig")
    new_emails = {
        row.get("email", "").strip().lower()
        for row in csv.DictReader(io.StringIO(text))
        if row.get("email")
    }

    db = await get_connection()
    try:
        cur = await db.execute("SELECT email FROM email_users")
        all_emails = {row[0] for row in await cur.fetchall()}
        to_delete = all_emails - new_emails
        for email in to_delete:
            await db.execute("DELETE FROM email_users WHERE email = ?", (email,))
        await db.commit()
    finally:
        await db.close()

    # синхронизируем ботов
    await sync_users_from_csv(content)

    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    # ... остальной код без изменений ...
