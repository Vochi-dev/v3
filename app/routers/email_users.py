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
        logger.debug("Executing SQL for list_email_users: %s", sql.strip())
        cur = await db.execute(sql)
        rows = await cur.fetchall()
        logger.debug("Fetched %d rows from email_users", len(rows))
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
        if email and email not in new_emails:
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
            {"request": request, "to_remove": to_remove, "csv_b64": csv_b64},
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
                    int(row.get("right_1")   or 0),
                    int(row.get("right_2")   or 0),
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
    confirm: str   = Form(...)
):
    require_login(request)
    if confirm != "yes":
        return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    try:
        raw  = base64.b64decode(csv_b64.encode())
        text = raw.decode("utf-8-sig")
    except Exception:
        raise HTTPException(status_code=400, detail="Неверные данные CSV")

    new_set = {
        r["email"].strip().lower()
        for r in csv.DictReader(io.StringIO(text))
        if r.get("email")
    }

    db = await get_connection()
    try:
        cur = await db.execute("SELECT email, tg_id, bot_token FROM telegram_users")
        rows = await cur.fetchall()
        for row in rows:
            email = (row["email"] or "").strip().lower()
            tg_id = row["tg_id"]
            bot_token = row["bot_token"]
            if email and email not in new_set:
                await db.execute("DELETE FROM telegram_users WHERE email = ?", (email,))
                if bot_token and tg_id:
                    try:
                        bot = AiogramBot(token=bot_token)
                        logger.info(f"Отправляю сообщение об удалении пользователю {tg_id} через бота {bot_token}")
                        await bot.send_message(
                            chat_id=int(tg_id),
                            text="⛔️ Ваш доступ был отозван администратором."
                        )
                    except Exception as e:
                        logger.warning(f"Ошибка при отправке сообщения об удалении {tg_id}: {e}")

        await db.execute("DELETE FROM email_users")
        await db.commit()
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
                    int(row.get("right_1")   or 0),
                    int(row.get("right_2")   or 0),
                )
            )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    # 1. Сначала ищем bot_token в telegram_users
    bot_token = None
    email = None

    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        cur2 = await db2.execute(
            "SELECT bot_token, email FROM telegram_users WHERE tg_id = ?",
            (tg_id,)
        )
        row2 = await cur2.fetchone()
        if row2 and row2["bot_token"]:
            bot_token = row2["bot_token"]
            email = row2["email"]
            logger.info(f"Нашли bot_token через telegram_users: {bot_token}")
        else:
            logger.info("bot_token не найден через telegram_users")

    # 2. Если всё равно не нашли — пробуем enterprise_users (должно быть пусто в твоей структуре)
    if not bot_token:
        async with aiosqlite.connect(DB_PATH) as db2:
            db2.row_factory = aiosqlite.Row
            cur2 = await db2.execute("""
                SELECT e.bot_token
                FROM enterprise_users u
                JOIN enterprises e ON u.enterprise_id = e.number
                WHERE u.telegram_id = ?
                AND u.status = 'approved'
            """, (tg_id,))
            row2 = await cur2.fetchone()
            if row2 and row2["bot_token"]:
                bot_token = row2["bot_token"]
                logger.info(f"Нашли bot_token через enterprise_users: {bot_token}")
            else:
                logger.info("bot_token не найден через enterprise_users")

    # 3. Отправляем сервисное сообщение, если нашли бот
    if bot_token:
        try:
            bot = AiogramBot(token=bot_token)
            logger.info(f"Отправляю сообщение об удалении пользователю {tg_id} через бота {bot_token}")
            await bot.send_message(
                chat_id=int(tg_id),
                text="❌ Ваш доступ к боту был отозван администратором."
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение об удалении пользователю {tg_id}: {e}")
    else:
        logger.error(f"Не удалось найти bot_token для пользователя {tg_id}, сообщение не отправлено!")

    # 4. Удаляем пользователя из БД
    db = await get_connection()
    try:
        await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)
