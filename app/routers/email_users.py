# app/routers/email_users.py
# -*- coding: utf-8 -*-

import csv
import io
import base64
import logging
from typing import List, Dict, Optional

import aiosqlite
from fastapi import (
    APIRouter, Request, status,
    HTTPException, UploadFile, File, Form, Query
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
logger.debug(f"=== LOADED email_users.py AT {__file__} ===")


@router.get("", response_class=HTMLResponse)
async def list_email_users(
    request: Request,
    selected: Optional[int] = None,
    group: str = Query(default="", alias="group"),
):
    require_login(request)
    group_mode = (group == "1")
    logger.debug(f"DEBUG: group_mode = {group_mode}")

    db = await get_connection()
    db.row_factory = lambda c, r: {c.description[i][0]: r[i] for i in range(len(r))}
    try:
        rows = await (await db.execute(
            "SELECT eu.number, eu.email, eu.name, eu.right_all, eu.right_1, eu.right_2,"
            " tu.tg_id, COALESCE(ent_app.name, ent_bot.name, '') AS enterprise_name"
            " FROM email_users eu"
            " LEFT JOIN telegram_users tu ON tu.email = eu.email"
            " LEFT JOIN enterprise_users ue_app ON ue_app.telegram_id = tu.tg_id AND ue_app.status='approved'"
            " LEFT JOIN enterprises ent_app ON ent_app.number = ue_app.enterprise_id"
            " LEFT JOIN enterprises ent_bot ON ent_bot.bot_token = tu.bot_token"
            " ORDER BY eu.number, eu.email"
        )).fetchall()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows, "selected_tg": selected, "group_mode": group_mode}
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
        rec = await (await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,)
        )).fetchone()
    finally:
        await db.close()

    if not rec or not rec.get("bot_token"):
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    success, error = await send_message_to_bot(rec["bot_token"], str(tg_id), message)
    if not success:
        raise HTTPException(status_code=500, detail=f"Не удалось отправить сообщение: {error}")
    return RedirectResponse("/admin/email-users", status_code=303)


@router.post("/message-group", response_class=RedirectResponse)
async def message_group(request: Request, message: str = Form(...)):
    require_login(request)
    db = await get_connection()
    try:
        rows = await (await db.execute(
            "SELECT tu.tg_id, tu.bot_token FROM telegram_users tu"
            " INNER JOIN email_users eu ON tu.email = eu.email"
            " WHERE tu.verified = 1"
        )).fetchall()
    finally:
        await db.close()

    for tg_id, bot_token in rows:
        if tg_id and bot_token:
            try:
                success, error = await send_message_to_bot(bot_token, str(tg_id), message)
                if not success:
                    logger.warning(f"Не удалось отправить групповое сообщение {tg_id}: {error}")
            except Exception as e:
                logger.warning(f"Не удалось отправить групповое сообщение {tg_id}: {e}")

    return RedirectResponse("/admin/email-users", status_code=303)


@router.post("/upload", response_class=HTMLResponse)
async def upload_email_users(request: Request, file: UploadFile = File(...)):
    require_login(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Только CSV")

    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    new_emails = {r["email"].strip().lower() for r in reader if r.get("email")}

    db = await get_connection()
    try:
        old = await (await db.execute(
            "SELECT eu.email, tu.tg_id, tu.bot_token FROM email_users eu"
            " LEFT JOIN telegram_users tu ON tu.email = eu.email"
        )).fetchall()
    finally:
        await db.close()

    to_remove = []
    for r in old:
        em = (r.get("email") or "").strip().lower()
        if em and em not in new_emails and r.get("tg_id"):
            unit = ""
            if r.get("bot_token"):
                db2 = await get_connection()
                try:
                    row2 = await (await db2.execute(
                        "SELECT name FROM enterprises WHERE bot_token = ?", (r["bot_token"],)
                    )).fetchone()
                    unit = row2["name"] if row2 else ""
                finally:
                    await db2.close()
            to_remove.append({"tg_id": r["tg_id"], "email": r["email"], "enterprise_name": unit})

    if to_remove:
        return templates.TemplateResponse(
            "confirm_sync.html",
            {
                "request": request,
                "to_remove": to_remove,
                "csv_b64": base64.b64encode(text.encode()).decode()
            },
            status_code=status.HTTP_200_OK
        )

    db = await get_connection()
    try:
        await db.execute("DELETE FROM email_users")
        for row in csv.DictReader(io.StringIO(text)):
            await db.execute(
                "INSERT INTO email_users(number, email, name, right_all, right_1, right_2)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row.get("number"), row.get("email"), row.get("name"),
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

    text = base64.b64decode(csv_b64.encode()).decode("utf-8-sig")
    new_set = {r["email"].strip().lower() for r in csv.DictReader(io.StringIO(text)) if r.get("email")}

    db = await get_connection()
    db.row_factory = aiosqlite.Row
    try:
        rows = await (await db.execute(
            "SELECT email, tg_id, bot_token FROM telegram_users"
        )).fetchall()

        for r in rows:
            email, tg_id, bot_token = r["email"], r["tg_id"], r["bot_token"]
            if email and email not in new_set and tg_id and bot_token:
                success, error = await send_message_to_bot(bot_token, str(tg_id), "❌ Ваш доступ отозван администратором.")
                if not success:
                    logger.warning(f"Не удалось уведомить пользователя {tg_id} об отзыве доступа: {error}")
                await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
                await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
                await db.execute("DELETE FROM email_users WHERE email = ?", (email,))
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=303)


#
# DELETE Handlers — полностью копируют логику CSV-удаления
#

# 1) Показываем страницу подтверждения
@router.post("/delete/{tg_id}", response_class=HTMLResponse)
async def delete_user_confirm(tg_id: int, request: Request):
    require_login(request)

    db = await get_connection()
    db.row_factory = aiosqlite.Row
    try:
        user = await (await db.execute(
            "SELECT email, tg_id FROM telegram_users WHERE tg_id = ?", (tg_id,)
        )).fetchone()
    finally:
        await db.close()

    if not user:
        return RedirectResponse("/admin/email-users", status_code=303)

    return templates.TemplateResponse(
        "confirm_delete.html",
        {"request": request, "user": dict(user)}
    )


# 2) После «Да» — уведомляем и удаляем из трёх таблиц
@router.post("/delete/confirm", response_class=RedirectResponse)
async def delete_user_execute(
    request: Request,
    tg_id: int = Form(...),
    confirm: str = Form(...)
):
    require_login(request)
    if confirm != "yes":
        return RedirectResponse("/admin/email-users", status_code=303)

    # Получаем email и bot_token
    db = await get_connection()
    db.row_factory = aiosqlite.Row
    try:
        row = await (await db.execute(
            "SELECT email, bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,)
        )).fetchone()
        if not row:
            return RedirectResponse("/admin/email-users", status_code=303)
        email, bot_token = row["email"], row["bot_token"]
    finally:
        await db.close()

    # Шлём уведомление
    if bot_token:
        await send_message_to_bot(bot_token, tg_id, "❌ Ваш доступ отозван администратором.")

    # Удаляем из email_users, enterprise_users и telegram_users
    db2 = await get_connection()
    try:
        await db2.execute("DELETE FROM email_users WHERE email = ?", (email,))
        await db2.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db2.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db2.commit()
    finally:
        await db2.close()

    return RedirectResponse("/admin/email-users", status_code=303)
