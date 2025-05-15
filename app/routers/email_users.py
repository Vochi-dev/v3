# app/routers/email_users.py
# -*- coding: utf-8 -*-
import csv
import io
import base64
from typing import List, Dict

import aiosqlite
from fastapi import (
    APIRouter, Request, status, HTTPException,
    UploadFile, File, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot as AiogramBot

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.db import get_connection
from app.services.user_sync import find_users_to_remove, perform_sync

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
              tu.tg_id           AS tg_id,
              eu.email,
              eu.name,
              eu.right_all,
              eu.right_1,
              eu.right_2,
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

    # 1) читаем CSV-контент
    content = await file.read()  # bytes
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    # 2) обновляем таблицу email_users
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

    # 3) собираем список пользователей, которых CSV больше не содержит
    to_remove: List[Dict] = await find_users_to_remove(content)

    if not to_remove:
        # если удалять некого — выполняем удаление сразу (оно ничего не сделает) и уходим
        await perform_sync(content)
        return RedirectResponse(
            url="/admin/email-users",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # 4) иначе показываем страницу подтверждения
    csv_b64 = base64.b64encode(content).decode()
    return templates.TemplateResponse(
        "confirm_sync.html",
        {
            "request": request,
            "to_remove": to_remove,
            "csv_b64": csv_b64,
        }
    )


@router.post("/upload/confirm", response_class=HTMLResponse)
async def confirm_upload(
    request: Request,
    csv_b64: str = Form(...),
    confirm: str = Form(...)
):
    require_login(request)

    # если админ отменил — просто возвращаемся к списку
    if confirm != "yes":
        return RedirectResponse(
            url="/admin/email-users",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # декодируем CSV и выполняем удаление + уведомление
    try:
        content = base64.b64decode(csv_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Неверные данные CSV")

    await perform_sync(content)
    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    # находим bot_token для этого пользователя
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

    # удаляем из БД
    db = await get_connection()
    try:
        await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db.commit()
    finally:
        await db.close()

    # уведомляем через бот
    if bot_token:
        try:
            bot = AiogramBot(token=bot_token)
            await bot.send_message(
                chat_id=tg_id,
                text="❌ Ваш доступ к боту был отозван администратором."
            )
        except Exception:
            pass

    return RedirectResponse(
        url="/admin/email-users",
        status_code=status.HTTP_303_SEE_OTHER
    )
