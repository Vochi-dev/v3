# app/routers/admin.py
# -*- coding: utf-8 -*-

import logging
import subprocess
import csv
import io
import base64
from datetime import datetime

from fastapi import (
    APIRouter, Request, Form, status, HTTPException,
    File, UploadFile
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telegram import Bot
from telegram.error import TelegramError

from app.config import ADMIN_PASSWORD
from app.services.db import get_connection
from app.services.bot_status import check_bot_status
from app.services.enterprise import send_message_to_bot
from app.services.database import update_enterprise

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("admin")


def require_login(request: Request):
    if request.cookies.get("auth") != "1":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.get("", response_class=HTMLResponse)
async def root_redirect(request: Request):
    if request.cookies.get("auth") == "1":
        return RedirectResponse("/admin/dashboard", status_code=status.HTTP_302_FOUND)
    return RedirectResponse("/admin/login", status_code=status.HTTP_302_FOUND)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    resp = RedirectResponse("/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("auth", "1", httponly=True)
    return resp


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_login(request)
    db = await get_connection()
    db.row_factory = None
    cur = await db.execute("SELECT COUNT(*) AS cnt FROM enterprises")
    row = await cur.fetchone()
    await db.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "enterprise_count": row[0]}
    )


@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    logger.info("list_enterprises called")
    require_login(request)
    db = await get_connection()
    db.row_factory = lambda c, r: {c.description[i][0]: r[i] for i in range(len(r))}
    cur = await db.execute("""
        SELECT number, name, bot_token, active,
               chat_id, ip, secret, host, name2
          FROM enterprises
         ORDER BY CAST(number AS INTEGER) ASC
    """)
    rows = await cur.fetchall()
    await db.close()

    enterprises_with_status = []
    for ent in rows:
        try:
            ent["bot_available"] = await check_bot_status(ent["bot_token"])
        except Exception:
            ent["bot_available"] = False
        enterprises_with_status.append(ent)

    try:
        result = subprocess.run(["pgrep", "-fl", "bot.py"], capture_output=True, text=True)
        bots_running = bool(result.stdout.strip())
    except Exception:
        bots_running = False

    return templates.TemplateResponse(
        "enterprises.html",
        {
            "request": request,
            "enterprises": enterprises_with_status,
            "service_running": True,
            "bots_running": bots_running,
        }
    )


@router.get("/enterprises/add", response_class=HTMLResponse)
async def add_enterprise_form(request: Request):
    require_login(request)
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "add", "enterprise": {}}
    )


@router.post("/enterprises/add", response_class=RedirectResponse)
async def add_enterprise(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form("")
):
    require_login(request)
    created_at = datetime.utcnow().isoformat()
    db = await get_connection()
    try:
        await db.execute(
            """
            INSERT INTO enterprises(
              number, name, bot_token, chat_id,
              ip, secret, host, created_at, name2
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (number, name, bot_token, chat_id, ip, secret, host, created_at, name2)
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_form(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    db.row_factory = None
    cur = await db.execute(
        """
        SELECT number, name, bot_token, active,
               chat_id, ip, secret, host, name2
          FROM enterprises
         WHERE number = ?
        """,
        (number,)
    )
    ent = await cur.fetchone()
    await db.close()
    if not ent:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    ent_dict = {
        "number": ent[0],
        "name": ent[1],
        "bot_token": ent[2],
        "active": ent[3],
        "chat_id": ent[4],
        "ip": ent[5],
        "secret": ent[6],
        "host": ent[7],
        "name2": ent[8],
    }
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "edit", "enterprise": ent_dict}
    )


@router.post("/enterprises/{number}/edit", response_class=RedirectResponse)
async def edit_enterprise(
    request: Request,
    number: str,
    name: str = Form(...),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form("")
):
    require_login(request)
    db = await get_connection()
    try:
        await db.execute(
            """
            UPDATE enterprises
               SET name=?, bot_token=?,
                   chat_id=?, ip=?, secret=?, host=?, name2=?
             WHERE number=?
            """,
            (name, bot_token, chat_id, ip, secret, host, name2, number)
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/enterprises/{number}", response_class=JSONResponse)
async def delete_enterprise(number: str, request: Request):
    require_login(request)
    db = await get_connection()
    await db.execute("DELETE FROM enterprises WHERE number = ?", (number,))
    await db.commit()
    await db.close()
    return JSONResponse({"detail": "Enterprise deleted"})


@router.post("/enterprises/{number}/send_message", response_class=JSONResponse)
async def send_message(number: str, request: Request):
    require_login(request)
    data = await request.json()
    message = data.get("message")
    if not message:
        return JSONResponse({"detail": "Message is required"}, status_code=400)

    db = await get_connection()
    db.row_factory = None
    cur = await db.execute(
        "SELECT bot_token, chat_id FROM enterprises WHERE number = ?", (number,)
    )
    row = await cur.fetchone()
    await db.close()

    if not row:
        return JSONResponse({"detail": "Enterprise not found"}, status_code=404)

    bot_token, chat_id = row
    try:
        success = await send_message_to_bot(bot_token, chat_id, message)
        if success:
            logger.info(f"Message sent successfully to enterprise #{number}")
            return JSONResponse({"detail": "Message sent"})
        else:
            return JSONResponse({"detail": "Failed to send message"}, status_code=500)
    except Exception as e:
        logger.error(f"Failed to send message to bot {number}: {e}", exc_info=True)
        return JSONResponse({"detail": "Failed to send message"}, status_code=500)


# ——————————————————————————————————————————————————————————————————————————
# Работа с email_users
# ——————————————————————————————————————————————————————————————————————————

@router.get("/email-users", response_class=HTMLResponse)
async def email_users_page(request: Request):
    require_login(request)
    db = await get_connection()
    db.row_factory = lambda c, r: {c.description[i][0]: r[i] for i in range(len(r))}
    cur = await db.execute("""
        SELECT
          eu.number               AS number,
          eu.email                AS email,
          eu.name                 AS name,
          eu.right_all            AS right_all,
          eu.right_1              AS right_1,
          eu.right_2              AS right_2,
          tu.tg_id                AS tg_id,
          COALESCE(ent.name, '')  AS enterprise_name
        FROM email_users eu
        LEFT JOIN telegram_users tu ON tu.email = eu.email
        LEFT JOIN enterprises ent ON ent.number = eu.number
        ORDER BY eu.number, eu.email
    """)
    rows = await cur.fetchall()
    await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows}
    )


@router.post("/email-users/upload", response_class=HTMLResponse)
async def upload_email_users(
    request: Request,
    file: UploadFile = File(...),
    confirm: str | None = Form(None),
    csv_b64: str | None = Form(None),
):
    require_login(request)

    # Повторное подтверждение
    if confirm:
        # раскодируем CSV
        data = base64.b64decode(csv_b64.encode())
        text = data.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))

        db = await get_connection()
        try:
            # удаляем из telegram_users тех, кого больше нет
            cur_exist = await db.execute("SELECT email, tg_id, bot_token FROM telegram_users")
            exist = await cur_exist.fetchall()
            new_emails = {row["email"].strip().lower() for row in reader}
            reader = csv.DictReader(io.StringIO(text))  # сброс итератора

            for email, tg_id, bot_token in exist:
                if email.lower() not in new_emails:
                    await db.execute("DELETE FROM telegram_users WHERE email = ?", (email,))
                    # уведомляем пользователя
                    try:
                        bot = Bot(token=bot_token)
                        await bot.send_message(chat_id=int(tg_id),
                                               text="⚠️ Администратор отозвал ваш доступ.")
                    except TelegramError:
                        pass

            # перезаполняем email_users
            await db.execute("DELETE FROM email_users")
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
                        int(row.get("right_all", 0)),
                        int(row.get("right_1", 0)),
                        int(row.get("right_2", 0)),
                    )
                )
            await db.commit()
        finally:
            await db.close()

        return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    # Первый заход: читаем новый CSV
    content = await file.read()
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    new_emails = {r["email"].strip().lower() for r in reader}
    csv_b64_val = base64.b64encode(text.encode()).decode()

    db = await get_connection()
    try:
        # текущие telegram_users
        cur_exist = await db.execute("SELECT email, tg_id, bot_token FROM telegram_users")
        exist = await cur_exist.fetchall()

        to_remove = []
        for email, tg_id, bot_token in exist:
            if email.lower() not in new_emails:
                # узнаём юнит
                cur_ent = await db.execute(
                    "SELECT name FROM enterprises WHERE bot_token = ?", (bot_token,)
                )
                ent_row = await cur_ent.fetchone()
                unit = ent_row[0] if ent_row else ""
                to_remove.append({"tg_id": tg_id, "email": email, "enterprise_name": unit})

        # очищаем email_users на время подтверждения
        await db.execute("DELETE FROM email_users")
        await db.commit()
    finally:
        await db.close()

    # если есть исчезнувшие — показываем confirm
    if to_remove:
        return templates.TemplateResponse(
            "confirm_sync.html",
            {
                "request": request,
                "to_remove": to_remove,
                "csv_b64": csv_b64_val
            }
        )

    # нет конфликтов — сразу вставляем
    reader = csv.DictReader(io.StringIO(text))
    db = await get_connection()
    try:
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
                    int(row.get("right_all", 0)),
                    int(row.get("right_1", 0)),
                    int(row.get("right_2", 0)),
                )
            )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)
