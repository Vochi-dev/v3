# app/routers/email_users.py
# -*- coding: utf-8 -*-

import csv
import io
import base64
import logging
from typing import List, Dict

import aiosqlite
from fastapi import (
    APIRouter, Request, status,
    HTTPException, UploadFile, File, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.db import get_connection
from app.services.enterprise import send_message_to_bot

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("email_users")
logger.setLevel(logging.DEBUG)


@router.get("", response_class=HTMLResponse)
async def list_email_users(
    request: Request,
    selected: int | None = None,
    group: int | None = None,  # теперь int
):
    require_login(request)
    db = await get_connection()
    db.row_factory = lambda c, r: {c.description[i][0]: r[i] for i in range(len(r))}
    try:
        sql = """
        SELECT
          eu.number               AS number,
          eu.email                AS email,
          eu.name                 AS name,
          eu.right_all            AS right_all,
          eu.right_1              AS right_1,
          eu.right_2              AS right_2,
          tu.tg_id                AS tg_id,
          COALESCE(ent_app.name, ent_bot.name, '') AS enterprise_name
        FROM email_users eu
        LEFT JOIN telegram_users tu
          ON tu.email = eu.email
        LEFT JOIN enterprise_users ue_app
          ON ue_app.telegram_id = tu.tg_id
          AND ue_app.status = 'approved'
        LEFT JOIN enterprises ent_app
          ON ent_app.number = ue_app.enterprise_id
        LEFT JOIN enterprises ent_bot
          ON ent_bot.bot_token = tu.bot_token
        ORDER BY eu.number, eu.email
        """
        cur = await db.execute(sql)
        rows = await cur.fetchall()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {
            "request": request,
            "email_users": rows,
            "selected_tg": selected,
            "group_mode": (group == 1),  # true только при group=1
        }
    )


@router.post("/message/{tg_id}")
async def message_user(
    tg_id: int,
    message: str = Form(...),
    request: Request = None
):
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?",
            (tg_id,)
        )
        rec = await cur.fetchone()
    finally:
        await db.close()

    if not rec or not rec["bot_token"]:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    await send_message_to_bot(rec["bot_token"], tg_id, message)
    return RedirectResponse("/admin/email-users", status_code=303)


@router.post("/message-group", response_class=RedirectResponse)
async def message_group(
    request: Request,
    message: str = Form(...)
):
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            "SELECT tg_id, bot_token FROM telegram_users WHERE verified = 1"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    for r in rows:
        tg_id = r["tg_id"]
        bot_token = r["bot_token"]
        if tg_id and bot_token:
            try:
                await send_message_to_bot(bot_token, tg_id, message)
            except Exception as e:
                logger.warning(f"Не удалось отправить групповое сообщение {tg_id}: {e}")

    return RedirectResponse("/admin/email-users", status_code=303)


@router.post("/upload", response_class=HTMLResponse)
async def upload_email_users(request: Request, file: UploadFile = File(...)):
    require_login(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Файл должен быть в формате CSV")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    new_emails = {row["email"].strip().lower() for row in reader if row.get("email")}

    db = await get_connection()
    try:
        cur = await db.execute("""
            SELECT eu.email, tu.tg_id, tu.bot_token
            FROM email_users eu
            LEFT JOIN telegram_users tu ON tu.email = eu.email
        """)
        old = await cur.fetchall()
    finally:
        await db.close()

    to_remove: List[Dict] = []
    for r in old:
        email = (r["email"] or "").strip().lower()
        if email and email not in new_emails and r["tg_id"]:
            unit = ""
            if r["bot_token"]:
                db2 = await get_connection()
                try:
                    cur2 = await db2.execute(
                        "SELECT name FROM enterprises WHERE bot_token = ?",
                        (r["bot_token"],)
                    )
                    row2 = await cur2.fetchone()
                    unit = row2["name"] if row2 else ""
                finally:
                    await db2.close()
            to_remove.append({
                "tg_id": r["tg_id"],
                "email": r["email"],
                "enterprise_name": unit
            })

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

    db = await get_connection()
    try:
        await db.execute("DELETE FROM email_users")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            await db.execute(
                """
                INSERT INTO email_users(number, email, name, right_all, right_1, right_2)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("number"),
                    row.get("email"),
                    row.get("name"),
                    int(row.get("right_all") or 0),
                    int(row.get("right_1") or 0),
                    int(row.get("right_2") or 0),
                )
            )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=303)


@router.post("/upload/confirm", response_class=RedirectResponse)
async def confirm_upload(
    request: Request,
    csv_b64: str = Form(...),
    confirm: str = Form(...)
):
    require_login(request)
    if confirm != "yes":
        return RedirectResponse("/admin/email-users", status_code=303)

    raw = base64.b64decode(csv_b64.encode())
    text = raw.decode("utf-8-sig")
    new_set = {
        r["email"].strip().lower()
        for r in csv.DictReader(io.StringIO(text))
        if r.get("email")
    }

    db = await get_connection()
    db.row_factory = aiosqlite.Row
    try:
        cur = await db.execute("SELECT email, tg_id, bot_token FROM telegram_users")
        rows = await cur.fetchall()

        for row in rows:
            email = (row["email"] or "").strip().lower()
            tg_id = row["tg_id"]
            bot_token = row["bot_token"]
            if email and email not in new_set and tg_id and bot_token:
                await send_message_to_bot(
                    bot_token,
                    tg_id,
                    "❌ Ваш доступ к боту был отозван администратором."
                )
                await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
                await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
                await db.execute("DELETE FROM email_users WHERE email = ?", (email,))

        await db.execute("DELETE FROM email_users")
        reader = csv.DictReader(io.StringIO(text))
        for r in reader:
            await db.execute(
                """
                INSERT INTO email_users(number, email, name, right_all, right_1, right_2)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("number"),
                    r.get("email"),
                    r.get("name"),
                    int(r.get("right_all") or 0),
                    int(r.get("right_1") or 0),
                    int(r.get("right_2") or 0),
                )
            )

        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=303)


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?",
            (tg_id,)
        ).fetchone()
    if row and row["bot_token"]:
        bot_token = row["bot_token"]

    if not bot_token:
        logger.error(f"[delete] bot_token не найден для tg_id={tg_id}")
        return RedirectResponse("/admin/email-users", status_code=303)

    try:
        await send_message_to_bot(
            bot_token,
            tg_id,
            "❌ Ваш доступ к боту был отозван администратором."
        )
        logger.info(f"[delete] уведомление отправлено {tg_id}")
    except Exception as e:
        logger.warning(f"[delete] не удалось отправить уведомление {tg_id}: {e}")

    db2 = await get_connection()
    try:
        await db2.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db2.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db2.execute(
            "DELETE FROM email_users WHERE email IN (SELECT email FROM telegram_users WHERE tg_id = ?)",
            (tg_id,)
        )
        await db2.commit()
        logger.info(f"[delete] все данные пользователя {tg_id} удалены")
    finally:
        await db2.close()

    return RedirectResponse("/admin/email-users", status_code=303)
