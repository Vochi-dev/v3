# app/routers/email_users.py
# -*- coding: utf-8 -*-
import csv
import io
import base64
import logging
from typing import List, Dict

import aiosqlite
from fastapi import (
    APIRouter, Request, status, HTTPException,
    UploadFile, File, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot as AiogramBot
from telegram.error import TelegramError

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.db import get_connection

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

logger = logging.getLogger("email_users")
logger.setLevel(logging.DEBUG)


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    require_login(request)
    db = await get_connection()
    # показываем сразу всю таблицу
    db.row_factory = aiosqlite.Row
    try:
        cur = await db.execute(
            """
            SELECT
              tu.tg_id           AS tg_id,
              eu.email           AS email,
              eu.name            AS name,
              eu.right_all       AS right_all,
              eu.right_1         AS right_1,
              eu.right_2         AS right_2,
              ent.name           AS enterprise_name
            FROM email_users eu
            LEFT JOIN telegram_users tu
              ON eu.email = tu.email
            LEFT JOIN enterprise_users uut
              ON uut.telegram_id = tu.tg_id
            LEFT JOIN enterprises ent
              ON uut.enterprise_id = ent.number
            ORDER BY eu.number, eu.email
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

    # Считаем CSV во временный буфер
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    new_emails = {row["email"].strip().lower() for row in reader if row.get("email")}

    # Берём из базы текущие email_users + их tg_id и unit
    db = await get_connection()
    try:
        cur = await db.execute(
            """
            SELECT
              eu.email            AS email,
              tu.tg_id            AS tg_id,
              tu.bot_token        AS bot_token,
              ent.name            AS enterprise_name
            FROM email_users eu
            LEFT JOIN telegram_users tu
              ON eu.email = tu.email
            LEFT JOIN enterprise_users uut
              ON uut.telegram_id = tu.tg_id
            LEFT JOIN enterprises ent
              ON uut.enterprise_id = ent.number
            """
        )
        old_rows = await cur.fetchall()
    finally:
        await db.close()

    # Вычисляем, кто выпадет
    to_remove: List[Dict] = []
    for r in old_rows:
        e = r["email"].strip().lower()
        if e not in new_emails:
            to_remove.append({
                "tg_id": r["tg_id"],
                "email": r["email"],
                "enterprise_name": r["enterprise_name"] or ""
            })

    # Если есть потери — показываем confirm_sync
    if to_remove:
        csv_b64 = base64.b64encode(text.encode()).decode()
        return templates.TemplateResponse(
            "confirm_sync.html",
            {
                "request": request,
                "to_remove": to_remove,
                "csv_b64": csv_b64
            },
            status_code=status.HTTP_200_OK
        )

    # Иначе — сразу переписываем email_users
    db = await get_connection()
    try:
        await db.execute("DELETE FROM email_users")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            await db.execute(
                """
                INSERT INTO email_users(number, email, name,
                                         right_all, right_1, right_2)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("number"),
                    row.get("email"),
                    row.get("name"),
                    int(row.get("right_all", 0)),
                    int(row.get("right_1", 0)),
                    int(row.get("right_2", 0)),
                )
            )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/upload/confirm", response_class=RedirectResponse)
async def confirm_upload(
    request: Request,
    csv_b64: str = Form(...),
    confirm: str = Form(...)
):
    require_login(request)

    # Если отказались — просто вернёмся
    if confirm != "yes":
        return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    # Декодируем CSV
    try:
        raw = base64.b64decode(csv_b64.encode())
        text = raw.decode("utf-8-sig")
    except Exception:
        raise HTTPException(status_code=400, detail="Неверные данные CSV")

    # Новый набор e-mail’ов
    reader = csv.DictReader(io.StringIO(text))
    new_set = {row["email"].strip().lower() for row in reader if row.get("email")}

    # 1) Извлекаем из telegram_users всех, чьи e-mail’ы исчезли, удаляем и уведомляем
    db = await get_connection()
    try:
        cur = await db.execute("SELECT email, tg_id, bot_token FROM telegram_users")
        for email, tg_id, bot_token in await cur.fetchall():
            if email.strip().lower() not in new_set:
                await db.execute("DELETE FROM telegram_users WHERE email = ?", (email,))
                # уведомляем
                try:
                    bot = AiogramBot(token=bot_token)
                    await bot.send_message(
                        chat_id=int(tg_id),
                        text="⛔️ Ваш доступ был отозван администратором."
                    )
                except TelegramError:
                    pass

        # 2) Синхронизируем email_users
        await db.execute("DELETE FROM email_users")
        await db.commit()
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            await db.execute(
                """
                INSERT INTO email_users(number, email, name,
                                         right_all, right_1, right_2)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("number"),
                    row.get("email"),
                    row.get("name"),
                    int(row.get("right_all", 0)),
                    int(row.get("right_1", 0)),
                    int(row.get("right_2", 0)),
                )
            )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    # Найдём bot_token по enterprise_users
    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        cur2 = await db2.execute(
            """
            SELECT e.bot_token
              FROM enterprise_users u
              JOIN enterprises e ON u.enterprise_id = e.number
             WHERE u.telegram_id = ?
            """,
            (tg_id,),
        )
        row2 = await cur2.fetchone()
        if row2:
            bot_token = row2["bot_token"]

    # Удаляем из telegram_users и enterprise_users
    db = await get_connection()
    try:
        await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db.commit()
    finally:
        await db.close()

    # Уведомляем вручную удалённого
    if bot_token:
        try:
            bot = AiogramBot(token=bot_token)
            await bot.send_message(
                chat_id=tg_id,
                text="❌ Ваш доступ к боту был отозван администратором."
            )
        except Exception:
            pass

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)
