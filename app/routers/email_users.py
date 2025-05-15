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
    require_login(request)

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате CSV")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    # 1) Собираем набор e-mail из нового CSV
    new_emails = {row.get("email", "").strip().lower() for row in reader if row.get("email")}
    if not new_emails:
        raise HTTPException(status_code=400, detail="В файле нет ни одного e-mail")

    # 2) Обновляем справочник email_users
    db = await get_connection()
    try:
        # Обновляем/добавляем только те, что в CSV
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

        # 3) Получаем старые e-mail до загрузки
        cur = await db.execute("SELECT email FROM email_users")
        old_emails = {row[0] for row in await cur.fetchall()}
    finally:
        await db.close()

    # 4) Определяем, кого предложить удалить (в old_emails ∖ new_emails)
    to_remove = sorted(old_emails - new_emails)

    # 5) Кодируем CSV для передачи в форму
    b64 = base64.b64encode(content).decode()

    return templates.TemplateResponse(
        "confirm_sync.html",
        {
            "request":   request,
            "to_remove": to_remove,
            "csv":       b64,
        }
    )


@router.post("/upload/confirm", response_class=RedirectResponse)
async def confirm_upload(
    request: Request,
    csv: str = Form(...),
    confirm: str = Form(...)
):
    require_login(request)

    # 1) Если админ отменил — откатываемся к списку
    if confirm != "yes":
        return RedirectResponse(
            url="/admin/email-users",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # 2) Раскодируем CSV
    content = base64.b64decode(csv.encode())
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    new_emails = {row.get("email", "").strip().lower() for row in reader if row.get("email")}

    # 3) Удаляем из email_users тех, кто не в new_emails
    db = await get_connection()
    try:
        # собираем список к удалению
        cur = await db.execute("SELECT email FROM email_users")
        all_emails = {row[0] for row in await cur.fetchall()}
        to_delete = all_emails - new_emails

        for email in to_delete:
            await db.execute("DELETE FROM email_users WHERE email = ?", (email,))
        await db.commit()
    finally:
        await db.close()

    # 4) Синхронизируем бот-пользователей (enterprise_users и telegram_users)
    #    удалит их из ботов, если e-mail больше не в CSV
    await sync_users_from_csv(content)

    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    """
    Кнопка «Delete» рядом со строкой.
    """
    require_login(request)

    # 1) Найдём bot_token
    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT e.bot_token
            FROM enterprise_users u
            JOIN enterprises e ON u.enterprise_id = e.number
            WHERE u.telegram_id = ?
            """,
            (tg_id,),
        )
        row = await cur.fetchone()
        if row:
            bot_token = row["bot_token"]

    # 2) Удаляем из БД
    db = await get_connection()
    try:
        await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db.execute("DELETE FROM telegram_users    WHERE tg_id       = ?", (tg_id,))
        await db.commit()
    finally:
        await db.close()

    # 3) Уведомляем
    if bot_token:
        from aiogram import Bot as AiogramBot
        bot = AiogramBot(token=bot_token)
        try:
            await bot.send_message(
                chat_id=tg_id,
                text="❌ Ваш доступ к боту был отозван администратором."
            )
        except:
            pass

    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )
