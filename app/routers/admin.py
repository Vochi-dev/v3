# -*- coding: utf-8 -*-
#
# Админский роутер для FastAPI
# ——————————————————————————————————————————————————————————————————————————
import logging
import subprocess
import csv
import io
import base64
import asyncio
import time
from datetime import datetime, timedelta

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
import jwt
import uuid
import aiohttp
import requests
import json
import re

from app.config import ADMIN_PASSWORD, DB_PATH, JWT_SECRET_KEY
from app.services.db import get_connection
from app.services.bot_status import check_bot_status
from app.services.enterprise import send_message_to_bot
from app.services.database import update_enterprise
from app.services.fail2ban import get_banned_ips, get_banned_count
from app.services.postgres import get_all_enterprises as get_all_enterprises_postgresql, get_pool
import asyncpg
from telegram import Bot
from telegram.error import TelegramError

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("admin")
logger.setLevel(logging.DEBUG)

# Функция для подключения к PostgreSQL
async def get_postgres_connection():
    """Создание подключения к PostgreSQL"""
    try:
        connection = await asyncpg.connect(
            host="localhost",
            port=5432,
            user="postgres", 
            password="r/Yskqh/ZbZuvjb2b3ahfg==",
            database="postgres"
        )
        return connection
    except Exception as e:
        logger.error(f"Ошибка подключения к PostgreSQL: {e}")
        return None

async def send_telegram_notification(telegram_id: int, bot_token: str, message: str) -> bool:
    """Отправка уведомления пользователю в Telegram"""
    if not telegram_id or not bot_token:
        return False
        
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=telegram_id, text=message)
        logger.info(f"📱 Telegram уведомление отправлено пользователю {telegram_id}")
        return True
    except TelegramError as e:
        logger.error(f"Ошибка отправки Telegram уведомления: {e}")
        return False

# Create a handler for log_action.txt
log_action_handler = logging.FileHandler("log_action.txt")
log_action_handler.setLevel(logging.DEBUG)
log_action_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(log_action_handler)


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
        secure=True,  # HTTPS (автоматическое перенаправление)  
        samesite="lax",  # Защита от CSRF
        max_age=2592000,  # 30 дней в секундах
    )
    return resp


# ——————————————————————————————————————————————————————————————————————————
# Дашборд
# ——————————————————————————————————————————————————————————————————————————

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    require_login(request)
    try:
        all_enterprises = await get_all_enterprises_postgresql()
        # Фильтруем предприятия: оставляем только активные (где active == True)
        active_enterprises = [ent for ent in all_enterprises if ent.get('active') is True]
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "enterprises": active_enterprises 
            }
        )
    except Exception as e:
        logger.error(f"Error in admin_dashboard: {e}")
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "enterprises": [],
                "error": str(e)
            }
        )


# ——————————————————————————————————————————————————————————————————————————
# Proxy для получения баланса WebSMS
# ——————————————————————————————————————————————————————————————————————————

@router.get("/proxy-balance", response_class=JSONResponse)
async def proxy_websms_balance(request: Request):
    """Proxy для получения баланса WebSMS от сервиса SMS (порт 8013)"""
    require_login(request)
    
    try:
        # Делаем запрос к SMS сервису на localhost:8013
        response = requests.get(
            "http://localhost:8013/balance",
            timeout=10,
            headers={'Accept': 'application/json'}
        )
        
        if response.status_code == 200:
            # Возвращаем JSON ответ как есть
            return response.json()
        else:
            logger.warning(f"SMS service balance request failed with status {response.status_code}")
            return {
                "success": False,
                "error": f"SMS service returned status {response.status_code}"
            }
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to SMS service for balance: {e}")
        return {
            "success": False,
            "error": "Unable to connect to SMS service"
        }
    except Exception as e:
        logger.error(f"Unexpected error in proxy_websms_balance: {e}")
        return {
            "success": False,
            "error": "Internal server error"
        }


# ——————————————————————————————————————————————————————————————————————————
# Управление сервисами системы
# ——————————————————————————————————————————————————————————————————————————

@router.get("/services", response_class=JSONResponse)
async def get_services_status(request: Request):
    """Получение статуса всех сервисов из all.sh"""
    require_login(request)
    
    try:
        # Определяем сервисы и их порты (из all.sh)
        services_info = {
            "admin": {"port": 8004, "script": "admin.sh", "app": "admin"},
            "dial": {"port": 8005, "script": "dial.sh", "app": "dial"},
            "111": {"port": 8000, "script": "111.sh", "app": "main"},
            "plan": {"port": 8006, "script": "plan.sh", "app": "plan"},
            "sms": {"port": 8002, "script": "sms.sh", "app": "goip_sms_service"},
            "sms_send": {"port": 8013, "script": "sms_send.sh", "app": "send_service_sms"},
            "send_user_sms": {"port": 8014, "script": "send_user_sms.sh", "app": "send_user_sms"},
            "auth": {"port": 8015, "script": "auth.sh", "app": "auth"},
            "telegram": {"port": 8016, "script": "telegram.sh", "app": "telegram_auth_service"},
            "download": {"port": 8007, "script": "download.sh", "app": "download"},
            "goip": {"port": 8008, "script": "goip.sh", "app": "goip_service"},
            "desk": {"port": 8011, "script": "desk.sh", "app": "desk"},
            "call": {"port": 8012, "script": "call.sh", "app": "call"},
            "miniapp": {"port": 8017, "script": "miniapp.sh", "app": "miniapp_service"},
            "asterisk": {"port": 8018, "script": "asterisk.sh", "app": "asterisk"},
            "reboot": {"port": 8009, "script": "reboot.sh", "app": "reboot.py"},
            "ewelink": {"port": 8010, "script": "ewelink.sh", "app": "ewelink_api"}
        }
        
        services = []
        
        for service_name, info in services_info.items():
            try:
                # Проверяем статус сервиса
                status = "unknown"
                
                if info["port"]:
                    # Проверяем по порту - более надежный метод
                    netstat_result = subprocess.run(
                        ["netstat", "-tlnp"], 
                        capture_output=True, 
                        text=True, 
                        timeout=5
                    )
                    # Проверяем и 0.0.0.0 и 127.0.0.1 привязки
                    port_pattern = f":{info['port']}"
                    port_found = port_pattern in netstat_result.stdout
                    
                    # Дополнительная проверка по процессу для download (может не показывать порт сразу)
                    if not port_found and service_name == "download":
                        ps_result = subprocess.run(
                            ["ps", "aux"], 
                            capture_output=True, 
                            text=True, 
                            timeout=5
                        )
                        if "uvicorn download:app" in ps_result.stdout:
                            status = "running"
                        else:
                            status = "stopped" 
                    else:
                        status = "running" if port_found else "stopped"
                else:
                    # Для сервисов без порта проверяем по процессу
                    ps_result = subprocess.run(
                        ["ps", "aux"], 
                        capture_output=True, 
                        text=True, 
                        timeout=5
                    )
                    # Используем более гибкий поиск процесса
                    if service_name == "reboot" and "reboot.py" in ps_result.stdout:
                        status = "running"
                    elif service_name == "ewelink" and "ewelink_api" in ps_result.stdout:
                        status = "running"
                    elif info["app"] in ps_result.stdout:
                        status = "running"
                    else:
                        status = "stopped"
                
                services.append({
                    "name": service_name,
                    "script": info["script"],
                    "app": info["app"],
                    "port": info["port"],
                    "status": status
                })
                
            except Exception as e:
                logger.error(f"Error checking status for service {service_name}: {e}")
                services.append({
                    "name": service_name,
                    "script": info["script"],
                    "app": info["app"],
                    "port": info["port"],
                    "status": "error"
                })
        
        # Сортируем сервисы по номеру порта (сначала с портами, потом без портов)
        services.sort(key=lambda x: (x["port"] is None, x["port"] if x["port"] is not None else 9999))
        
        return {
            "success": True,
            "services": services
        }
        
    except Exception as e:
        logger.error(f"Error getting services status: {e}")
        return {
            "success": False,
            "error": "Failed to get services status"
        }



@router.post("/services/{service_name}/action", response_class=JSONResponse)
async def control_service(service_name: str, request: Request):
    """Управление сервисом (stop/restart)"""
    # require_login(request)  # ОТКЛЮЧЕНО
    
    try:
        # Получаем действие из JSON body
        body = await request.json()
        action = body.get("action")
        
        if action not in ["stop", "restart"]:
            return {"success": False, "error": "Invalid action"}
        
        # Просто выполняем команду
        script_path = f"./{service_name}.sh"
        result = subprocess.run([script_path, action], capture_output=True, text=True, cwd="/root/asterisk-webhook")
        
        return {
            "success": result.returncode == 0,
            "message": f"{service_name} {action} completed",
            "output": result.stdout if result.returncode == 0 else result.stderr
        }
            
    except Exception as e:
        return {"success": False, "error": str(e)}


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
        success, error = await send_message_to_bot(bot_token, str(tg_id), message)
        if not success:
            logger.error(f"Не удалось отправить сообщение {tg_id}: {error}")
            raise HTTPException(status_code=500, detail=f"Не удалось отправить сообщение: {error}")
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
    """Удаляет все предприятия и связанные с ними данные из БД (SQLite)."""
    require_login(request)
    try:
        db = await get_connection()
        await db.execute("DELETE FROM enterprises")
        await db.commit()
        await db.close()
        return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "All enterprises have been deleted."})
    except Exception as e:
        logger.error(f"Failed to delete all enterprises: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete enterprises.")

@router.get("/generate-auth-token/{enterprise_number}", response_class=JSONResponse)
async def generate_auth_token(enterprise_number: str, request: Request):
    # require_login(request)  # Отключаем для доступа из рабочего стола
    logger.info(f"Запрос на генерацию токена для предприятия {enterprise_number}")
    payload = {
        "sub": enterprise_number,
        "is_admin": True,
        "exp": datetime.utcnow() + timedelta(minutes=5)  # Токен живёт 5 минут
    }
    try:
        token = jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")
        logger.info(f"Токен для предприятия {enterprise_number} успешно сгенерирован.")
        return JSONResponse({"token": token})
    except Exception as e:
        logger.error(f"Ошибка при генерации токена для предприятия {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка генерации токена")


@router.get("/config/{enterprise_number}", response_class=HTMLResponse)
async def get_enterprise_config(enterprise_number: str, request: Request):
    """Получение конфигурационных файлов с удаленного хоста предприятия"""
    require_login(request)
    
    try:
        # Получаем IP адрес предприятия из базы данных
        all_enterprises = await get_all_enterprises_postgresql()
        enterprise = None
        for ent in all_enterprises:
            if ent.get('number') == enterprise_number:
                enterprise = ent
                break
        
        if not enterprise:
            raise HTTPException(status_code=404, detail="Предприятие не найдено")
        
        ip = enterprise.get('ip')
        if not ip:
            raise HTTPException(status_code=400, detail="IP адрес предприятия не указан")
        
        # Список файлов для загрузки
        config_files = [
            '/etc/asterisk/extensions.conf',
            '/etc/asterisk/sip_addproviders.conf', 
            '/etc/asterisk/sip.conf',
            '/etc/network/interfaces',
            '/etc/rc.firewall'
        ]
        
        files_content = {}
        
        # Получаем содержимое каждого файла
        for file_path in config_files:
            try:
                # SSH команда для получения содержимого файла и его даты изменения
                cmd = [
                    'sshpass', '-p', '5atx9Ate@pbx',
                    'ssh', 
                    '-o', 'ConnectTimeout=10',
                    '-o', 'StrictHostKeyChecking=no',
                    '-o', 'UserKnownHostsFile=/dev/null',
                    '-o', 'LogLevel=ERROR',
                    '-p', '5059',
                    f'root@{ip}',
                    f'if [ -f "{file_path}" ]; then echo "FILE_DATE:$(stat -c %y "{file_path}")"; cat "{file_path}"; else echo "FILE_NOT_FOUND"; fi'
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
                
                if process.returncode == 0:
                    output = stdout.decode('utf-8', errors='ignore')
                    if output.startswith('FILE_DATE:'):
                        lines = output.split('\n', 1)
                        date_line = lines[0]
                        file_content = lines[1] if len(lines) > 1 else ""
                        
                        # Извлекаем дату файла
                        file_date = date_line.replace('FILE_DATE:', '').strip()
                        
                        files_content[file_path] = {
                            'content': file_content,
                            'date': file_date,
                            'status': 'success'
                        }
                    elif 'FILE_NOT_FOUND' in output:
                        files_content[file_path] = {
                            'content': '',
                            'date': '',
                            'status': 'not_found'
                        }
                    else:
                        files_content[file_path] = {
                            'content': output,
                            'date': 'Unknown',
                            'status': 'success'
                        }
                else:
                    error_msg = stderr.decode('utf-8', errors='ignore')
                    files_content[file_path] = {
                        'content': f'Ошибка получения файла: {error_msg}',
                        'date': '',
                        'status': 'error'
                    }
                    
            except asyncio.TimeoutError:
                files_content[file_path] = {
                    'content': 'Таймаут при получении файла',
                    'date': '',
                    'status': 'timeout'
                }
            except Exception as e:
                files_content[file_path] = {
                    'content': f'Ошибка: {str(e)}',
                    'date': '',
                    'status': 'error'
                }
        
        # Возвращаем HTML страницу с конфигурациями
        return templates.TemplateResponse(
            "enterprise_config.html",
            {
                "request": request,
                "enterprise_number": enterprise_number,
                "enterprise_name": enterprise.get('name', 'Unknown'),
                "enterprise_ip": ip,
                "files_content": files_content
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении конфигурации предприятия {enterprise_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка получения конфигурации: {str(e)}")


# ——————————————————————————————————————————————————————————————————————————
# Проверка GoIP устройств
# ——————————————————————————————————————————————————————————————————————————

async def check_goip_devices() -> dict:
    """Проверка GoIP устройств с custom_boolean_flag = true"""
    try:
        start_time = time.time()
        
        # Получаем список GoIP устройств с флагом custom_boolean_flag = true
        import asyncpg
        conn = await asyncpg.connect(
            host='localhost',
            port=5432,
            database='postgres',
            user='postgres',
            password='r/Yskqh/ZbZuvjb2b3ahfg=='
        )
        
        # SQL запрос для получения GoIP с флагом custom_boolean_flag = true
        query = """
        SELECT g.enterprise_number, g.gateway_name, g.port, g.device_model, 
               e.name as enterprise_name, g.custom_boolean_flag
        FROM goip g
        JOIN enterprises e ON g.enterprise_number = e.number
        WHERE g.custom_boolean_flag = true
        AND g.port IS NOT NULL
        """
        
        rows = await conn.fetch(query)
        await conn.close()
        
        if not rows:
            logger.info("No GoIP devices with custom_boolean_flag = true found")
            return {
                'success': True,
                'total_goip_devices': 0,
                'online_goip_devices': 0,
                'goip_devices': [],
                'checked_at': datetime.now().isoformat(),
                'total_time_ms': int((time.time() - start_time) * 1000)
            }
        
        # Проверяем каждое GoIP устройство параллельно
        tasks = []
        for row in rows:
            task = check_single_goip_device(
                row['enterprise_number'],
                row['gateway_name'], 
                row['port'],
                row['device_model'],
                row['enterprise_name']
            )
            tasks.append(task)
        
        # Ждем завершения всех проверок
        results = await asyncio.gather(*tasks)
        
        # Подсчитываем статистику
        online_count = sum(1 for result in results if result['status'] == 'online')
        total_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"Checked {len(results)} GoIP devices in {total_time}ms. Online: {online_count}")
        
        return {
            'success': True,
            'total_goip_devices': len(results),
            'online_goip_devices': online_count,
            'goip_devices': results,
            'checked_at': datetime.now().isoformat(),
            'total_time_ms': total_time
        }
        
    except Exception as e:
        logger.error(f"Error checking GoIP devices: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'total_goip_devices': 0,
            'online_goip_devices': 0,
            'goip_devices': [],
            'checked_at': datetime.now().isoformat(),
            'total_time_ms': int((time.time() - start_time) * 1000) if 'start_time' in locals() else 0
        }


async def check_single_goip_device(enterprise_number: str, gateway_name: str, port: int, device_model: str, enterprise_name: str) -> dict:
    """Проверка одного GoIP устройства через HTTP запрос к веб-интерфейсу"""
    start_time = time.time()
    
    try:
        # URL для проверки GoIP устройства через mftp.vochi.by
        url = f"http://mftp.vochi.by:{port}/"
        
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                response_time = int((time.time() - start_time) * 1000)
                
                # GoIP устройства обычно отвечают 200 или 303 (редирект)
                if response.status in [200, 303]:
                    logger.debug(f"GoIP {gateway_name} (enterprise {enterprise_number}) is online: HTTP {response.status}")
                    return {
                        'enterprise_number': enterprise_number,
                        'gateway_name': gateway_name,
                        'port': port,
                        'device_model': device_model,
                        'enterprise_name': enterprise_name,
                        'status': 'online',
                        'response_time_ms': response_time,
                        'http_status': response.status,
                        'error_message': None
                    }
                else:
                    # Неожиданный HTTP статус
                    error_msg = f"Unexpected HTTP status: {response.status}"
                    logger.warning(f"GoIP {gateway_name} (enterprise {enterprise_number}) returned HTTP {response.status}")
                    return {
                        'enterprise_number': enterprise_number,
                        'gateway_name': gateway_name,
                        'port': port,
                        'device_model': device_model,
                        'enterprise_name': enterprise_name,
                        'status': 'offline',
                        'response_time_ms': response_time,
                        'http_status': response.status,
                        'error_message': error_msg
                    }
                    
    except asyncio.TimeoutError:
        response_time = int((time.time() - start_time) * 1000)
        logger.warning(f"GoIP {gateway_name} (enterprise {enterprise_number}) timeout")
        return {
            'enterprise_number': enterprise_number,
            'gateway_name': gateway_name,
            'port': port,
            'device_model': device_model,
            'enterprise_name': enterprise_name,
            'status': 'offline',
            'response_time_ms': response_time,
            'http_status': None,
            'error_message': 'Connection timeout'
        }
    except Exception as e:
        response_time = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        logger.error(f"GoIP {gateway_name} (enterprise {enterprise_number}) error: {error_msg}")
        return {
            'enterprise_number': enterprise_number,
            'gateway_name': gateway_name,
            'port': port,
            'device_model': device_model,
            'enterprise_name': enterprise_name,
            'status': 'offline',
            'response_time_ms': response_time,
            'http_status': None,
            'error_message': error_msg
        }

# ——————————————————————————————————————————————————————————————————————————
# Мониторинг хостов Asterisk
# ——————————————————————————————————————————————————————————————————————————

def parse_sip_peers(output: str) -> dict:
    """
    Парсинг вывода команды 'sip show peers' для анализа типов линий
    
    Возвращает словарь с подсчетом по типам:
    - gsm_total: общее количество GSM линий (000xxxx)
    - gsm_online: количество онлайн GSM линий
    - sip_total: общее количество SIP линий (не 000xxxx, не 3-значные, не 301/302)
    - sip_online: количество онлайн SIP линий  
    - internal_total: количество внутренних линий (3-значные, кроме 301/302)
    - internal_online: количество онлайн внутренних линий
    """
    lines = output.strip().split('\n')
    
    gsm_total = 0
    gsm_online = 0
    sip_total = 0
    sip_online = 0
    internal_total = 0
    internal_online = 0
    
    for line in lines:
        # Пропускаем заголовки и служебные строки
        if 'Name/username' in line or 'sip peers' in line or not line.strip():
            continue
            
        # Парсим строку peer'а
        parts = line.split()
        if len(parts) < 6:
            continue
            
        name_part = parts[0]  # Например: "0001363/s" или "150/150"
        
        # Извлекаем имя peer'а (до slash)
        peer_name = name_part.split('/')[0]
        
        # Определяем статус (ищем "OK" в строке)
        is_online = " OK " in line
        
        # Классифицируем по типам
        if peer_name.startswith('000') and len(peer_name) == 7:
            # GSM линии (000xxxx)
            gsm_total += 1
            if is_online:
                gsm_online += 1
                
        elif len(peer_name) == 3 and peer_name.isdigit():
            # Внутренние линии (3-значные), кроме 301/302
            if peer_name not in ['301', '302']:
                internal_total += 1
                if is_online:
                    internal_online += 1
                    
        elif peer_name not in ['301', '302']:
            # SIP линии (все остальные, кроме 301/302)
            sip_total += 1
            if is_online:
                sip_online += 1
    
    return {
        'gsm_total': gsm_total,
        'gsm_online': gsm_online,
        'sip_total': sip_total,
        'sip_online': sip_online,
        'internal_total': internal_total,
        'internal_online': internal_online
    }


async def check_single_host(ip: str, enterprise_number: str) -> dict:
    """Проверка одного хоста Asterisk через SSH с детальным анализом линий и информацией о диске"""
    start_time = time.time()
    
    try:
        # SSH команда для проверки SIP peers
        sip_cmd = [
            'sshpass', '-p', '5atx9Ate@pbx',
            'ssh', 
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            '-p', '5059',
            f'root@{ip}',
            'timeout 10 asterisk -rx "sip show peers"'
        ]
        
        # SSH команда для проверки диска (получаем процент использования корневого раздела)
        df_cmd = [
            'sshpass', '-p', '5atx9Ate@pbx',
            'ssh', 
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            '-p', '5059',
            f'root@{ip}',
            'df -h / | tail -1 | awk \'{print $5}\' | sed \'s/%//\''
        ]
        
        # Выполняем обе команды параллельно
        sip_process = await asyncio.create_subprocess_exec(
            *sip_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        df_process = await asyncio.create_subprocess_exec(
            *df_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            # Ждем завершения обеих команд
            (sip_stdout, sip_stderr), (df_stdout, df_stderr) = await asyncio.gather(
                asyncio.wait_for(sip_process.communicate(), timeout=10.0),
                asyncio.wait_for(df_process.communicate(), timeout=10.0)
            )
            
            response_time = int((time.time() - start_time) * 1000)  # в миллисекундах
            
            # Анализируем результат SIP команды
            if sip_process.returncode == 0:
                # Успешное выполнение SIP команды - анализируем SIP peers
                sip_output = sip_stdout.decode('utf-8', errors='ignore')
                line_stats = parse_sip_peers(sip_output)
                total_peers = line_stats['gsm_total'] + line_stats['sip_total'] + line_stats['internal_total']
                
                # Анализируем результат df команды
                disk_usage_percent = None
                if df_process.returncode == 0:
                    try:
                        disk_usage_str = df_stdout.decode('utf-8', errors='ignore').strip()
                        disk_usage_percent = int(disk_usage_str)
                    except (ValueError, TypeError):
                        logger.warning(f"Не удалось распарсить процент использования диска для {ip}: '{disk_usage_str}'")
                        disk_usage_percent = None
                else:
                    logger.warning(f"Команда df завершилась с ошибкой для {ip}: {df_stderr.decode()}")
                
                return {
                    'enterprise_number': enterprise_number,
                    'ip': ip,
                    'status': 'online',
                    'response_time_ms': response_time,
                    'sip_peers': total_peers,  # Для обратной совместимости
                    'line_stats': line_stats,  # Детальная статистика
                    'disk_usage_percent': disk_usage_percent,  # Процент использования диска
                    'error_message': None
                }
            else:
                # Ошибка выполнения SIP команды
                error_output = sip_stderr.decode('utf-8', errors='ignore')
                return {
                    'enterprise_number': enterprise_number,
                    'ip': ip,
                    'status': 'offline',
                    'response_time_ms': response_time,
                    'sip_peers': None,
                    'line_stats': None,
                    'disk_usage_percent': None,
                    'error_message': error_output.strip() or 'Command failed'
                }
                
        except asyncio.TimeoutError:
            return {
                'enterprise_number': enterprise_number,
                'ip': ip,
                'status': 'offline',
                'response_time_ms': int((time.time() - start_time) * 1000),
                'sip_peers': None,
                'line_stats': None,
                'disk_usage_percent': None,
                'error_message': 'Connection timeout'
            }
            
    except Exception as e:
        response_time = int((time.time() - start_time) * 1000)
        return {
            'enterprise_number': enterprise_number,
            'ip': ip,
            'status': 'offline',
            'response_time_ms': response_time,
            'sip_peers': None,
            'line_stats': None,
            'disk_usage_percent': None,
            'error_message': str(e)
        }


@router.get("/check-hosts", response_class=JSONResponse)
async def check_hosts(request: Request):
    """Проверка всех активных хостов предприятий и GoIP устройств"""
    require_login(request)
    
    try:
        start_time = time.time()
        
        # Получаем активные предприятия с IP адресами
        all_enterprises = await get_all_enterprises_postgresql()
        active_enterprises = [
            ent for ent in all_enterprises 
            if ent.get('active') is True 
            and ent.get('is_enabled') is True 
            and ent.get('ip') is not None 
            and ent.get('ip').strip() != ''
        ]
        
        # Проверяем хосты и GoIP устройства параллельно
        host_tasks = []
        for enterprise in active_enterprises:
            task = check_single_host(enterprise['ip'], enterprise['number'])
            host_tasks.append(task)
        
        # Запускаем проверку хостов и GoIP параллельно
        if host_tasks:
            hosts_result, goip_result = await asyncio.gather(
                asyncio.gather(*host_tasks),
                check_goip_devices()
            )
        else:
            hosts_result = []
            goip_result = await check_goip_devices()
        
        # Подсчитываем статистику
        online_count = sum(1 for result in hosts_result if result['status'] == 'online')
        total_time = int((time.time() - start_time) * 1000)
        
        # Логируем результат
        logger.info(f"Checked {len(hosts_result)} hosts and {goip_result.get('total_goip_devices', 0)} GoIP devices in {total_time}ms. Online hosts: {online_count}, Online GoIP: {goip_result.get('online_goip_devices', 0)}")
        
        return JSONResponse({
            'success': True,
            'total_hosts': len(hosts_result),
            'online_hosts': online_count,
            'hosts': hosts_result,
            'goip_devices': goip_result,
            'checked_at': datetime.now().isoformat(),
            'total_time_ms': total_time
        })
        
    except Exception as e:
        logger.error(f"Error checking hosts: {e}", exc_info=True)
        return JSONResponse({
            'success': False,
            'error': str(e),
            'total_hosts': 0,
            'online_hosts': 0,
            'hosts': [],
            'goip_devices': {
                'success': False,
                'total_goip_devices': 0,
                'online_goip_devices': 0,
                'goip_devices': [],
                'error': str(e)
            },
            'checked_at': datetime.now().isoformat(),
            'total_time_ms': 0
        }, status_code=500)


@router.get("/live-events-today", response_class=JSONResponse)
async def get_live_events_today(request: Request):
    """Получить количество live событий за текущий день по предприятиям"""
    require_login(request)
    
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get('http://localhost:8007/sync/live/today') as response:
                if response.status == 200:
                    data = await response.json()
                    return JSONResponse(data)
                else:
                    logger.error(f"Ошибка получения статистики из download service: {response.status}")
                    return JSONResponse({
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "total_unsuccessful_events_today": 0,
                        "by_enterprise": {},
                        "error": "Сервис синхронизации недоступен"
                    }, status_code=500)
                    
    except Exception as e:
        logger.error(f"Ошибка получения статистики live событий: {e}", exc_info=True)
        return JSONResponse({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_unsuccessful_events_today": 0,
            "by_enterprise": {},
            "error": str(e)
        }, status_code=500)

@router.get("/test-goip", response_class=JSONResponse)
async def test_goip():
    """Тестовый эндпоинт для проверки GoIP функционала без авторизации"""
    try:
        start_time = time.time()
        goip_result = await check_goip_devices()
        
        return JSONResponse({
            'success': True,
            'goip_devices': goip_result,
            'check_time_ms': round((time.time() - start_time) * 1000, 2)
        })
        
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e),
            'goip_devices': []
        })

@router.get("/check-internal-phones-ip/{enterprise_number}", response_class=JSONResponse)
async def check_internal_phones_ip(enterprise_number: str, request: Request):
    """Получение IP адресов регистрации внутренних линий для конкретного предприятия"""
    require_login(request)
    
    try:
        # Получаем информацию о предприятии
        all_enterprises = await get_all_enterprises_postgresql()
        enterprise = next((ent for ent in all_enterprises if ent['number'] == enterprise_number), None)
        
        if not enterprise:
            return JSONResponse({'success': False, 'error': 'Enterprise not found'}, status_code=404)
            
        if not enterprise.get('ip'):
            return JSONResponse({'success': False, 'error': 'Enterprise IP not configured'}, status_code=400)
        
        # SSH команда для получения детальной информации о SIP peers
        cmd = [
            'sshpass', '-p', '5atx9Ate@pbx',
            'ssh', 
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            '-p', '5059',
            f'root@{enterprise["ip"]}',
            'timeout 15 asterisk -rx "sip show peers"'
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
            
            if process.returncode != 0:
                error_output = stderr.decode('utf-8', errors='ignore')
                return JSONResponse({
                    'success': False, 
                    'error': f'SSH command failed: {error_output.strip() or "Unknown error"}'
                }, status_code=500)
            
            # Парсим вывод команды sip show peers
            output = stdout.decode('utf-8', errors='ignore')
            lines = output.strip().split('\n')
            
            internal_phones = {}
            
            for line in lines:
                # Пропускаем заголовки и служебные строки
                if 'Name/username' in line or 'sip peers' in line or not line.strip():
                    continue
                
                # Парсим строку: "150/150         (Unspecified)    D  No         No             0    UNREACHABLE"
                # или: "151/151         192.168.1.100    D  Yes        Yes            0    OK (1 ms)"
                parts = line.split()
                if len(parts) < 6:
                    continue
                
                name_part = parts[0]  # Например: "150/150"
                ip_part = parts[1] if len(parts) > 1 else "(Unspecified)"
                
                # Извлекаем номер внутренней линии
                peer_name = name_part.split('/')[0]
                
                # Проверяем, что это внутренняя линия (3-значная, кроме 301/302)
                if len(peer_name) == 3 and peer_name.isdigit() and peer_name not in ['301', '302']:
                    # Определяем статус регистрации
                    status = 'online' if ' OK ' in line else 'offline'
                    
                    # Извлекаем IP адрес
                    if ip_part == '(Unspecified)' or ip_part == 'Unspecified':
                        ip_address = None
                    else:
                        # IP может быть в формате "192.168.1.100:5060" или просто "192.168.1.100"
                        ip_address = ip_part.split(':')[0]
                    
                    internal_phones[peer_name] = {
                        'phone_number': peer_name,
                        'ip_address': ip_address,
                        'status': status,
                        'raw_line': line.strip()
                    }
            
            logger.info(f"Found {len(internal_phones)} internal phones for enterprise {enterprise_number}")
            
            return JSONResponse({
                'success': True,
                'enterprise_number': enterprise_number,
                'enterprise_ip': enterprise['ip'],
                'internal_phones': internal_phones,
                'total_found': len(internal_phones),
                'checked_at': datetime.now().isoformat()
            })
            
        except asyncio.TimeoutError:
            return JSONResponse({
                'success': False, 
                'error': 'SSH connection timeout'
            }, status_code=500)
            
    except Exception as e:
        logger.error(f"Error checking internal phones IP for enterprise {enterprise_number}: {e}", exc_info=True)
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.get("/reboot-events-today", response_class=JSONResponse)
async def get_reboot_events_today(request: Request):
    """Получить счетчик событий перезагрузки за текущий день для всех предприятий"""
    require_login(request)
    
    try:
        pool = await get_pool()
        if not pool:
            return JSONResponse({'success': False, 'error': 'Database connection failed'}, status_code=500)
        
        async with pool.acquire() as conn:
            # SQL запрос для подсчета событий перезагрузки за текущий день
            query = """
                SELECT 
                    enterprise_number,
                    COUNT(*) as reboot_count
                FROM unit_status_history
                WHERE 
                    DATE(change_time) = CURRENT_DATE
                    AND (
                        (action_type = 'goip_reboot' AND action_result = 'success')
                        OR 
                        (action_type = 'ewelink_toggle_on' AND new_status = 'on' AND action_result = 'success')
                        OR
                        (action_type IS NULL AND new_status = 'on')
                    )
                GROUP BY enterprise_number
            """
            
            rows = await conn.fetch(query)
            
            # Преобразуем результат в словарь
            reboot_counts = {}
            for row in rows:
                reboot_counts[row['enterprise_number']] = row['reboot_count']
                
            return JSONResponse({
                'success': True,
                'reboot_counts': reboot_counts
            })
            
    except Exception as e:
        logger.error(f"Error getting reboot events: {e}", exc_info=True)
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)

# ============================================
# TELEGRAM ПОЛЬЗОВАТЕЛИ
# ============================================

@router.get("/telegram-users-by-enterprise/{enterprise_number}")
async def get_telegram_users_by_enterprise(enterprise_number: str):
    """Получить Telegram-пользователей конкретного предприятия"""
    conn = await get_postgres_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        users = await conn.fetch("""
            SELECT u.id, u.email, u.first_name, u.last_name,
                   u.telegram_authorized, u.telegram_tg_id, u.telegram_auth_blocked,
                   u.personal_phone, u.created_at,
                   CASE 
                       WHEN u.telegram_authorized THEN u.created_at
                       ELSE NULL
                   END as telegram_auth_date
            FROM users u
            WHERE u.enterprise_number = $1
            ORDER BY u.telegram_authorized DESC, u.last_name, u.first_name
        """, enterprise_number)
        
        return {
            "success": True,
            "enterprise_number": enterprise_number,
            "users": [dict(user) for user in users]
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения Telegram пользователей для {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        await conn.close()

@router.post("/revoke-telegram-auth/{user_id}")
async def revoke_telegram_auth_admin(user_id: int):
    """Отзыв Telegram-авторизации пользователя (для суперадмина)"""
    conn = await get_postgres_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        # Получаем данные пользователя и предприятия перед удалением
        user = await conn.fetchrow("""
            SELECT u.telegram_tg_id, u.email, u.enterprise_number, e.bot_token, e.name as enterprise_name
            FROM users u
            JOIN enterprises e ON u.enterprise_number = e.number
            WHERE u.id = $1
        """, user_id)
        
        if not user:
            return {"success": False, "message": "Пользователь не найден"}
        
        if not user['telegram_tg_id']:
            return {"success": False, "message": "Пользователь не авторизован в Telegram"}
        
        # Сбрасываем авторизацию
        await conn.execute("""
            UPDATE users 
            SET telegram_authorized = FALSE,
                telegram_tg_id = NULL,
                telegram_auth_code = NULL,
                telegram_auth_expires = NULL
            WHERE id = $1
        """, user_id)
        
        # Удаляем из telegram_users (если есть такая таблица)
        try:
            await conn.execute("""
                DELETE FROM telegram_users WHERE tg_id = $1
            """, user['telegram_tg_id'])
        except:
            # Таблица может не существовать, игнорируем ошибку
            pass
        
        # Отправляем уведомление пользователю
        notification_text = f"""⛔️ Ваша авторизация в Telegram-боте предприятия "{user['enterprise_name']}" была отозвана администратором.

Для повторной авторизации обратитесь к администратору предприятия."""
        
        await send_telegram_notification(
            telegram_id=user['telegram_tg_id'],
            bot_token=user['bot_token'],
            message=notification_text
        )
        
        logger.info(f"Отозвана Telegram-авторизация для пользователя {user['email']} (ID: {user_id})")
        return {"success": True, "message": "Telegram-авторизация отозвана"}
        
    except Exception as e:
        logger.error(f"Ошибка отзыва Telegram-авторизации: {e}")
        return {"success": False, "message": "Ошибка сервера"}
    finally:
        await conn.close()

@router.post("/block-telegram-auth/{user_id}")
async def block_telegram_auth(user_id: int, blocked: bool = Form(...)):
    """Блокировка/разблокировка Telegram-авторизации пользователя"""
    conn = await get_postgres_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        # Получаем данные пользователя и предприятия
        user_data = await conn.fetchrow("""
            SELECT u.telegram_tg_id, u.email, u.enterprise_number, u.telegram_authorized,
                   e.bot_token, e.name as enterprise_name
            FROM users u
            JOIN enterprises e ON u.enterprise_number = e.number
            WHERE u.id = $1
        """, user_id)
        
        if not user_data:
            return {"success": False, "message": "Пользователь не найден"}
        
        # Обновляем статус блокировки
        result = await conn.execute("""
            UPDATE users 
            SET telegram_auth_blocked = $1
            WHERE id = $2
        """, blocked, user_id)
        
        if result == "UPDATE 0":
            return {"success": False, "message": "Пользователь не найден"}
        
        # Если блокируем и пользователь был авторизован - отзываем авторизацию
        if blocked and user_data['telegram_authorized'] and user_data['telegram_tg_id']:
            await conn.execute("""
                UPDATE users 
                SET telegram_authorized = FALSE,
                    telegram_auth_code = NULL,
                    telegram_auth_expires = NULL
                WHERE id = $1
            """, user_id)
            
            # Удаляем из telegram_users
            try:
                await conn.execute("""
                    DELETE FROM telegram_users WHERE tg_id = $1
                """, user_data['telegram_tg_id'])
            except:
                pass
        
        # Отправляем уведомление пользователю
        if user_data['telegram_tg_id'] and user_data['bot_token']:
            if blocked:
                notification_text = f"""🚫 Ваш аккаунт заблокирован для использования Telegram-бота предприятия "{user_data['enterprise_name']}".

Обратитесь к администратору предприятия для разблокировки."""
            else:
                notification_text = f"""✅ Ваш аккаунт разблокирован для использования Telegram-бота предприятия "{user_data['enterprise_name']}".

Для авторизации введите команду /start или сразу напишите ваш корпоративный email."""
            
            await send_telegram_notification(
                telegram_id=user_data['telegram_tg_id'],
                bot_token=user_data['bot_token'],
                message=notification_text
            )
        
        action = "заблокирована" if blocked else "разблокирована"
        logger.info(f"Telegram-авторизация {action} для пользователя ID: {user_id}")
        
        return {
            "success": True, 
            "message": f"Telegram-авторизация {action}",
            "blocked": blocked
        }
        
    except Exception as e:
        logger.error(f"Ошибка блокировки Telegram-авторизации: {e}")
        return {"success": False, "message": "Ошибка сервера"}
    finally:
        await conn.close()

