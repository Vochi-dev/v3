# -*- coding: utf-8 -*-
#
# Админский роутер для FastAPI
# ——————————————————————————————————————————————————————————————————————————
import logging
import subprocess
import csv
import io
import base64
from datetime import datetime

import aiosqlite
from fastapi import (
    APIRouter, Request, Form, status, HTTPException,
    File, UploadFile, Depends, Query
)
from fastapi.responses import (
    HTMLResponse, RedirectResponse, JSONResponse
)
from fastapi.templating import Jinja2Templates
from telegram import Bot
from telegram.error import TelegramError

from app.config import ADMIN_PASSWORD, DB_PATH
from app.services.db import get_connection
from app.services.bot_status import check_bot_status
from app.services.enterprise import send_message_to_bot
from app.services.database import update_enterprise
from app.services.fail2ban import get_banned_ips, get_banned_count
from app.services.postgres import get_all_enterprises as get_all_enterprises_postgresql

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("admin")
logger.setLevel(logging.DEBUG)


def require_login(request: Request):
    if request.cookies.get("auth") != "1":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# ——————————————————————————————————————————————————————————————————————————
# Авторизация
# ——————————————————————————————————————————————————————————————————————————

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
    resp.set_cookie(
        "auth", "1",
        httponly=True,
        secure=True,  # Только HTTPS
        samesite="lax",  # Защита от CSRF
        max_age=2592000,  # 30 дней в секундах
    )
    return resp


# ——————————————————————————————————————————————————————————————————————————
# Дашборд
# ——————————————————————————————————————————————————————————————————————————

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    try:
        all_enterprises = await get_all_enterprises_postgresql()
        # Фильтруем предприятия: оставляем только активные (где active == True)
        active_enterprises = [ent for ent in all_enterprises if ent.get('active') is True]
        # Сортируем по номеру (предполагаем, что number можно преобразовать в int для корректной числовой сортировки)
        # Если number всегда числовой и хранится как строка, лучше явно преобразовывать или убедиться, что БД сортирует корректно
        # Функция get_all_enterprises уже сортирует по CAST(number AS INTEGER) ASC, так что дополнительная сортировка не нужна,
        # если фильтрация не нарушает порядок.
        # Но для надежности, если вдруг get_all_enterprises изменится, можно раскомментировать:
        # active_enterprises.sort(key=lambda x: int(x['number']))

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "enterprises": active_enterprises # Передаем отфильтрованные и отсортированные предприятия
            }
        )
    except Exception as e:
        # Логирование ошибки можно добавить здесь
        print(f"Error in admin_dashboard: {e}")
        # Можно вернуть страницу с ошибкой или пустой список
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "enterprises": [],
                "error": str(e)
            }
        )


# ——————————————————————————————————————————————————————————————————————————
# CRUD для предприятий (Закомментировано, чтобы использовать реализацию из app/routers/enterprise.py)
# ——————————————————————————————————————————————————————————————————————————

# @router.get("/enterprises", response_class=HTMLResponse)
# async def list_enterprises(request: Request):
#     require_login(request)
#     logger.debug("Listing enterprises")
#     db = await get_connection()
#     db.row_factory = lambda c, r: {c.description[i][0]: r[i] for i in range(len(r))}
#     cur = await db.execute("""
#         SELECT number, name, bot_token, active,
#                chat_id, ip, secret, host, name2
#           FROM enterprises
#          ORDER BY CAST(number AS INTEGER) ASC
#     """)
#     rows = await cur.fetchall()
#     await db.close()
# 
#     enterprises_with_status = []
#     for ent in rows:
#         try:
#             ent["bot_available"] = await check_bot_status(ent["bot_token"])
#         except Exception:
#             ent["bot_available"] = False
#         enterprises_with_status.append(ent)
# 
#     try:
#         result = subprocess.run(["pgrep", "-fl", "bot.py"], capture_output=True, text=True)
#         bots_running = bool(result.stdout.strip())
#     except Exception:
#         bots_running = False
# 
#     return templates.TemplateResponse(
#         "enterprises.html",
#         {
#             "request": request,
#             "enterprises": enterprises_with_status,
#             "service_running": True,
#             "bots_running": bots_running,
#         }
#     )
# 
# 
# @router.get("/enterprises/add", response_class=HTMLResponse)
# async def add_enterprise_form(request: Request):
#     require_login(request)
#     return templates.TemplateResponse(
#         "enterprise_form.html",
#         {"request": request, "action": "add", "enterprise": {}}
#     )
# 
# 
# @router.post("/enterprises/add", response_class=RedirectResponse)
# async def add_enterprise(
#     request: Request,
#     number: str = Form(...),
#     name: str = Form(...),
#     bot_token: str = Form(...),
#     chat_id: str = Form(...),
#     ip: str = Form(...),
#     secret: str = Form(...),
#     host: str = Form(...),
#     name2: str = Form("")
# ):
#     require_login(request)
#     created_at = datetime.utcnow().isoformat()
#     db = await get_connection()
#     try:
#         await db.execute(
#             """
#             INSERT INTO enterprises(
#               number, name, bot_token, chat_id,
#               ip, secret, host, created_at, name2
#             ) VALUES (?,?,?,?,?,?,?,?,?)
#             """,
#             (number, name, bot_token, chat_id, ip, secret, host, created_at, name2)
#         )
#         await db.commit()
#     finally:
#         await db.close()
#     return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)
# 
# 
# @router.get("/enterprises/{number}/edit", response_class=HTMLResponse)
# async def edit_enterprise_form(request: Request, number: str):
#     require_login(request)
#     db = await get_connection()
#     db.row_factory = None # Use tuple factory for this specific query
#     cur = await db.execute(
#         """
#         SELECT number, name, bot_token, active,
#                chat_id, ip, secret, host, name2
#           FROM enterprises
#          WHERE number = ?
#         """,
#         (number,)
#     )
#     ent_tuple = await cur.fetchone()
#     await db.close()
#     if not ent_tuple:
#         raise HTTPException(status_code=404, detail="Enterprise not found")
#     # Convert tuple to dict manually or ensure row_factory was set for dicts if preferred
#     ent_dict = {
#         "number": ent_tuple[0], "name": ent_tuple[1], "bot_token": ent_tuple[2],
#         "active": ent_tuple[3], "chat_id": ent_tuple[4], "ip": ent_tuple[5],
#         "secret": ent_tuple[6], "host": ent_tuple[7], "name2": ent_tuple[8],
#     }
#     return templates.TemplateResponse(
#         "enterprise_form.html",
#         {"request": request, "action": "edit", "enterprise": ent_dict}
#     )
# 
# 
# @router.post("/enterprises/{number}/edit", response_class=RedirectResponse)
# async def edit_enterprise(
#     request: Request,
#     number: str,
#     name: str = Form(...),
#     bot_token: str = Form(""),
#     chat_id: str = Form(""),
#     ip: str = Form(...),
#     secret: str = Form(...),
#     host: str = Form(...),
#     name2: str = Form("")
# ):
#     require_login(request)
#     # logger.debug(f"Updating enterprise {number} with data: name={name}, bot_token={bot_token}, chat_id={chat_id}, ip={ip}, secret={secret}, host={host}, name2={name2}")
#     # # Используем импортированную функцию update_enterprise, которая должна работать с PostgreSQL
#     # # Это предположение, что app.services.database.update_enterprise сконфигурирована для PG
#     # await update_enterprise(
#     #     number=number, name=name, bot_token=bot_token, chat_id=chat_id,
#     #     ip=ip, secret=secret, host=host, name2=name2
#     # )
#     # # Код ниже для SQLite, если update_enterprise выше не работает или не то
#     db = await get_connection()
#     try:
#         await db.execute(
#             """UPDATE enterprises
#                SET name=?, bot_token=?,
#                    chat_id=?, ip=?, secret=?, host=?, name2=?
#              WHERE number=?""",
#             (name, bot_token, chat_id, ip, secret, host, name2, number)
#         )
#         await db.commit()
#     except aiosqlite.Error as e:
#         logger.error(f"SQLite error during update of enterprise {number}: {e}")
#         # Optionally re-raise or handle more gracefully
#         raise HTTPException(status_code=500, detail=f"Database error: {e}")
#     finally:
#         await db.close()
#     return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)
# 
# 
# @router.delete("/enterprises/{number}", response_class=JSONResponse)
# async def delete_enterprise(number: str, request: Request): # Added request for require_login
#     require_login(request)
#     db = await get_connection()
#     try:
#         await db.execute("DELETE FROM enterprises WHERE number=?", (number,))
#         await db.commit()
#     finally:
#         await db.close()
#     return JSONResponse({"message": "Enterprise deleted"})
# 
# 
# @router.post("/enterprises/{number}/send_message", response_class=JSONResponse)
# async def send_message(number: str, request: Request):
#     require_login(request)
#     payload = await request.json()
#     message_text = payload.get("message")
#     if not message_text:
#         raise HTTPException(status_code=400, detail="Message cannot be empty")
# 
#     db = await get_connection()
#     db.row_factory = aiosqlite.Row # Ensure dict-like rows
#     cur = await db.execute("SELECT bot_token, chat_id FROM enterprises WHERE number=?", (number,))
#     ent = await cur.fetchone()
#     await db.close()
# 
#     if not ent:
#         raise HTTPException(status_code=404, detail="Enterprise not found")
#     if not ent["bot_token"] or not ent["chat_id"]:
#         raise HTTPException(status_code=400, detail="Enterprise bot_token or chat_id is missing")
# 
#     success = await send_message_to_bot(ent["bot_token"], ent["chat_id"], message_text)
#     if success:
#         return JSONResponse({"message": "Message sent successfully"})
#     else:
#         raise HTTPException(status_code=500, detail="Failed to send message")

# ——————————————————————————————————————————————————————————————————————————
# Работа с email_users
# ——————————————————————————————————————————————————————————————————————————

@router.get("/email-users", response_class=HTMLResponse)
async def email_users_page(request: Request):
    """
    Теперь показывает ВСЕ записи из email_users (даже без tg_id),
    подтягивает tg_id (если есть) и определяет Unit:
      — сначала по approved записи в enterprise_users,
      — иначе по bot_token из telegram_users.
    А также по query-param `selected` отображает форму отправки сообщения.
    """
    require_login(request)
    logger.debug("Display email_users page")

    # получить выбранный tg_id из параметров URL
    selected_param = request.query_params.get("selected")
    try:
        selected_tg = int(selected_param) if selected_param else None
    except ValueError:
        selected_tg = None

    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row

    sql = """
        SELECT
          eu.number               AS number,
          eu.email                AS email,
          eu.name                 AS name,
          eu.right_all            AS right_all,
          eu.right_1              AS right_1,
          eu.right_2              AS right_2,
          tu.tg_id                AS tg_id,
          COALESCE(ent_app.name,
                   ent_bot.name,
                   '')               AS enterprise_name
        FROM email_users eu
        LEFT JOIN telegram_users tu
          ON tu.email = eu.email
        -- приоритет 1: одобренные в enterprise_users
        LEFT JOIN enterprise_users ue_app
          ON ue_app.telegram_id = tu.tg_id
          AND ue_app.status = 'approved'
        LEFT JOIN enterprises ent_app
          ON ent_app.number = ue_app.enterprise_id
        -- приоритет 2: по bot_token
        LEFT JOIN enterprises ent_bot
          ON ent_bot.bot_token = tu.bot_token
        ORDER BY eu.number, eu.email
    """
    logger.debug("Executing SQL: %s", sql.replace("\n", " "))
    cur = await db.execute(sql)
    rows = await cur.fetchall()
    logger.debug("Fetched %d email_users rows", len(rows))
    await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {
            "request": request,
            "email_users": rows,
            "selected_tg": selected_tg,
        }
    )


@router.post("/email-users/upload", response_class=HTMLResponse)
async def upload_email_users(
    request: Request,
    file: UploadFile = File(...),
    confirm: str | None = Form(None),
    csv_b64: str | None = Form(None),
):
    require_login(request)

    # ——— Шаг 1: превью перед удалением ———
    if not confirm:
        content = await file.read()
        text = content.decode("utf-8-sig")
        logger.debug("Preview new CSV:\n%s", text)
        reader = csv.DictReader(io.StringIO(text))
        new_emails = {r["email"].strip().lower() for r in reader if r.get("email")}
        csv_b64_val = base64.b64encode(text.encode()).decode()

        db = await get_connection()
        try:
            cur = await db.execute("SELECT email, tg_id, bot_token FROM telegram_users")
            old = await cur.fetchall()
            logger.debug("Existing telegram_users count: %d", len(old))

            to_remove = []
            for email, tg_id, bot_token in old:
                if email.strip().lower() not in new_emails:
                    logger.debug("Will remove telegram_user %s", email)
                    c2 = await db.execute(
                        "SELECT name FROM enterprises WHERE bot_token = ?", (bot_token,)
                    )
                    row2 = await c2.fetchone()
                    unit = row2[0] if row2 else ""
                    to_remove.append({
                        "tg_id": tg_id,
                        "email": email,
                        "enterprise_name": unit
                    })
        finally:
            await db.close()

        if to_remove:
            logger.debug("to_remove list: %s", to_remove)
            return templates.TemplateResponse(
                "confirm_sync.html",
                {"request": request, "to_remove": to_remove, "csv_b64": csv_b64_val},
                status_code=status.HTTP_200_OK
            )

        # без удалений — сразу синхронизируем email_users
        db2 = await get_connection()
        try:
            await db2.execute("DELETE FROM email_users")
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                await db2.execute(
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
            await db2.commit()
            logger.debug("Synchronized email_users without deletions")
        finally:
            await db2.close()

        return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    # ——— Шаг 2: подтверждение удаления старых ———
    raw = base64.b64decode(csv_b64.encode())
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    new_set = {r["email"].strip().lower() for r in reader if r.get("email")}
    logger.debug("Confirm deletion, new_set=%s", new_set)

    db = await get_connection()
    try:
        cur = await db. execute("SELECT email, tg_id, bot_token FROM telegram_users")
        for email, tg_id, bot_token in await cur.fetchall():
            if email.strip().lower() not in new_set:
                logger.debug("Deleting telegram_user %s", email)
                await db.execute("DELETE FROM telegram_users WHERE email = ?", (email,))
                try:
                    bot = Bot(token=bot_token)
                    await bot.send_message(chat_id=int(tg_id),
                                           text="⛔️ Ваш доступ был отозван администратором.")
                except TelegramError:
                    logger.debug("Failed notifying %s", tg_id)

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
        logger.debug("Synchronized email_users after confirm")
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/email-users/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    # ——— Точно такая же логика удаления и уведомления, что и при CSV-sync ———

    # 1) Получаем токен предприятия (приоритет) для уведомления
    bot_token = None
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
        if row2:
            bot_token = row2["bot_token"]

    # 2) Узнаём email и доп. токен из telegram_users
    db = await get_connection()
    db.row_factory = aiosqlite.Row
    try:
        rec = await db.execute("SELECT email, bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,))
        row = await rec.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        email = row["email"]
        # если enterprise-токен не нашёлся, берём из telegram_users
        if not bot_token:
            bot_token = row["bot_token"]
    finally:
        await db.close()

    # 3) Уведомляем пользователя тем же сообщением
    if bot_token:
        try:
            bot = Bot(token=bot_token)
            await bot.send_message(
                chat_id=int(tg_id),
                text="❌ Доступ отозван администратором."
            )
        except TelegramError:
            logger.debug("Failed notifying via Bot API %s", tg_id)

    # 4) Полное удаление пользователя из всех таблиц
    async with aiosqlite.connect(DB_PATH) as db3:
        await db3.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db3.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db3.execute("DELETE FROM email_users WHERE email = ?", (email,))
        await db3.commit()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/email-users/message/{tg_id}", response_class=RedirectResponse)
async def send_admin_message(tg_id: int, request: Request, message: str = Form(...)):
    """
    Отправка произвольного сообщения Telegram-пользователю.
    """
    require_login(request)

    bot_token = None

    # Сначала — ищем токен по одобренным enterprise_users
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
        if row2:
            bot_token = row2["bot_token"]

    # Если не найдено — берём из telegram_users
    if not bot_token:
        async with aiosqlite.connect(DB_PATH) as db3:
            db3.row_factory = aiosqlite.Row
            cur3 = await db3.execute(
                "SELECT bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,)
            )
            row3 = await cur3.fetchone()
            if row3:
                bot_token = row3["bot_token"]

    if not bot_token:
        raise HTTPException(status_code=500, detail="Не удалось определить токен бота для пользователя")

    try:
        await send_message_to_bot(bot_token, tg_id, message)
    except Exception as e:
        logger.exception(f"Не удалось отправить сообщение {tg_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение")

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/banned_ips")
async def get_banned_ip_list():
    """Get list of banned IPs with country information"""
    return await get_banned_ips()

@router.get("/banned_count")
async def get_banned_ip_count():
    """Get count of banned IPs"""
    return {"count": await get_banned_count()}

@router.delete("/enterprises/all", response_class=JSONResponse)
async def delete_all_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    await db.execute("DELETE FROM enterprises;")
    await db.commit()
    await db.close()
    return JSONResponse({"detail": "All enterprises deleted"})
