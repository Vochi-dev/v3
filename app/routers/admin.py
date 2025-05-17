# -*- coding: utf-8 -*-
import aiosqlite
import logging
from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    Request,
    Form,
    status,
    HTTPException,
    Depends,
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telegram import Bot

from app.config import ADMIN_PASSWORD, DB_PATH
from app.services.database import get_connection
from app.services.enterprise import delete_enterprise, get_enterprise_by_number, update_enterprise, add_enterprise
from app.services.bot_status import check_bot_status

logger = logging.getLogger("admin")
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/admin", tags=["admin"])

# Простая авторизация через cookie
def require_login(request: Request):
    auth = request.cookies.get("auth")
    if auth != "1":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

# --- Авторизация и выход ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Страница логина с формой
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    resp = RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("auth", "1", httponly=True, max_age=3600 * 24)  # Кука на 1 день
    return resp

@router.get("/logout", response_class=RedirectResponse)
async def logout():
    resp = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie("auth")
    return resp

# --- Главная страница админки ---

@router.get("/", response_class=RedirectResponse)
async def root_redirect():
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_302_FOUND)

# --- Список предприятий ---

@router.get("/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Сортировка по возрастанию числового номера предприятия
        cursor = await db.execute("""
            SELECT number, name, bot_token, active, chat_id, ip, secret, name2
            FROM enterprises
            ORDER BY CAST(number AS INTEGER) ASC
        """)
        rows = await cursor.fetchall()

    enterprises = []
    for row in rows:
        ent = dict(row)
        try:
            ent["bot_available"] = await check_bot_status(ent["bot_token"])
        except Exception as e:
            logger.warning(f"Не удалось проверить статус бота для предприятия {ent['number']}: {e}")
            ent["bot_available"] = False
        enterprises.append(ent)

    return templates.TemplateResponse(
        "enterprises.html",
        {
            "request": request,
            "enterprises": enterprises,
        },
    )

# --- Добавление нового предприятия ---

@router.get("/enterprises/add", response_class=HTMLResponse)
async def add_enterprise_page(request: Request):
    require_login(request)
    return templates.TemplateResponse("enterprise_form.html", {"request": request, "enterprise": None, "error": None})

@router.post("/enterprises/add", response_class=HTMLResponse)
async def add_enterprise_post(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: Optional[str] = Form(""),
):
    require_login(request)

    try:
        await add_enterprise(number, name, bot_token, chat_id, ip, secret, host, name2)
    except Exception as e:
        logger.error(f"Ошибка при добавлении предприятия {number}: {e}")
        return templates.TemplateResponse(
            "enterprise_form.html",
            {
                "request": request,
                "enterprise": {
                    "number": number,
                    "name": name,
                    "bot_token": bot_token,
                    "chat_id": chat_id,
                    "ip": ip,
                    "secret": secret,
                    "host": host,
                    "name2": name2,
                },
                "error": str(e),
            },
        )
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

# --- Редактирование предприятия ---

@router.get("/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_page(request: Request, number: str):
    require_login(request)
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    return templates.TemplateResponse("enterprise_form.html", {"request": request, "enterprise": enterprise, "error": None})

@router.post("/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_post(
    request: Request,
    number: str,
    name: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: Optional[str] = Form(""),
):
    require_login(request)
    try:
        await update_enterprise(number, name, bot_token, chat_id, ip, secret, host, name2)
    except Exception as e:
        logger.error(f"Ошибка при редактировании предприятия {number}: {e}")
        enterprise = {
            "number": number,
            "name": name,
            "bot_token": bot_token,
            "chat_id": chat_id,
            "ip": ip,
            "secret": secret,
            "host": host,
            "name2": name2,
        }
        return templates.TemplateResponse("enterprise_form.html", {"request": request, "enterprise": enterprise, "error": str(e)})
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

# --- Удаление предприятия с подтверждением ---

@router.post("/enterprises/{number}/delete", response_class=JSONResponse)
async def delete_enterprise_endpoint(request: Request, number: str):
    require_login(request)
    try:
        await delete_enterprise(number)
        logger.info(f"Предприятие {number} удалено через админку")
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Ошибка при удалении предприятия {number}: {e}")
        return JSONResponse({"success": False, "error": str(e)})

# --- Отправка сообщения в бота предприятия с подтверждением ---

@router.post("/enterprises/{number}/send_message", response_class=JSONResponse)
async def send_message_to_bot(
    request: Request, number: str, message: str = Form(...)
):
    require_login(request)
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        return JSONResponse({"success": False, "error": "Enterprise not found"}, status_code=404)

    bot_token = enterprise["bot_token"]
    chat_id = enterprise["chat_id"]

    bot = Bot(token=bot_token)
    try:
        await bot.send_message(chat_id=int(chat_id), text=message)
        logger.info(f"Сообщение отправлено предприятию {number}")
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения предприятию {number}: {e}")
        return JSONResponse({"success": False, "error": str(e)})

# --- Статус бота ---

@router.post("/enterprises/{number}/toggle", response_class=RedirectResponse)
async def toggle_enterprise_active(request: Request, number: str):
    require_login(request)

    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Enterprise not found")

    new_status = 0 if enterprise["active"] else 1
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE enterprises SET active = ? WHERE number = ?", (new_status, number))
        await db.commit()

    bot_token = enterprise["bot_token"]
    chat_id = enterprise["chat_id"]

    bot = Bot(token=bot_token)
    text = f"✅ Сервис {'активирован' if new_status else 'деактивирован'}"
    try:
        await bot.send_message(chat_id=int(chat_id), text=text)
        logger.info(f"Отправлено уведомление о смене статуса предприятию {number}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления предприятию {number}: {e}")

    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

# --- Прочие вспомогательные маршруты и методы ---

# Можно добавить страницы справки, статистики, логи, и т.п. если нужно

# --- Конец файла ---
