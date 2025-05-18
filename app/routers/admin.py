# app/routers/admin.py
# -*- coding: utf-8 -*-

import logging
import asyncio
import subprocess
from datetime import datetime

from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telegram import Bot
from telegram.error import TelegramError

from app.config import ADMIN_PASSWORD
from app.services.db import get_connection
from app.services.bot_status import check_bot_status
from app.services.enterprise import send_message_to_bot

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
    # row_factory для удобной работы со словарями
    db.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    cur = await db.execute("""
        SELECT
          number, name, bot_token, active,
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
            logger.info(f"Enterprise #{ent['number']} - bot_available: {ent['bot_available']}")
        except Exception as e:
            logger.error(f"Error checking bot status for #{ent['number']}: {e}")
            ent["bot_available"] = False
        enterprises_with_status.append(ent)

    service_running = True
    bots_running = True

    return templates.TemplateResponse(
        "enterprises.html",
        {
            "request": request,
            "enterprises": enterprises_with_status,
            "service_running": service_running,
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
    bot_token: str = Form(...),
    active: int = Form(...),
    chat_id: str = Form(...),
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
               SET name=?, bot_token=?, active=?,
                   chat_id=?, ip=?, secret=?, host=?, name2=?
             WHERE number=?
            """,
            (name, bot_token, active, chat_id, ip, secret, host, name2, number)
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

    logger.info(f"send_message called for enterprise #{number} with payload: {data}")

    if not message:
        logger.warning(f"Empty message received for enterprise #{number}")
        return JSONResponse({"detail": "Message is required"}, status_code=400)

    db = await get_connection()
    db.row_factory = None
    cur = await db.execute("SELECT bot_token, chat_id FROM enterprises WHERE number = ?", (number,))
    row = await cur.fetchone()
    await db.close()

    if not row:
        logger.error(f"Enterprise #{number} not found in database")
        return JSONResponse({"detail": "Enterprise not found"}, status_code=404)

    bot_token, chat_id = row
    logger.info(f"Enterprise #{number} bot_token: {bot_token!r}, chat_id: {chat_id!r}")

    if not bot_token or not bot_token.strip():
        logger.error(f"Enterprise #{number} has no bot_token or it is empty")
        return JSONResponse({"detail": "Enterprise has no bot token"}, status_code=400)
    if not chat_id:
        logger.error(f"Enterprise #{number} has no chat_id or it is empty")
        return JSONResponse({"detail": "Enterprise has no chat_id"}, status_code=400)

    try:
        bot = Bot(token=bot_token)
        chat_id_int = int(chat_id)
        logger.info(f"Sending manual message to chat_id {chat_id_int} for enterprise #{number}")
        sent_message = await bot.send_message(chat_id=chat_id_int, text=message)
        logger.info(f"Message sent successfully: message_id={sent_message.message_id}")
    except Exception as e:
        logger.error(f"Failed to send manual message to bot {number}: {e}", exc_info=True)
        return JSONResponse({"detail": "Failed to send message"}, status_code=500)

    return JSONResponse({"detail": "Message sent"})


@router.post("/enterprises/{number}/toggle", response_class=RedirectResponse)
async def toggle_enterprise(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    db.row_factory = None
    cur = await db.execute(
        "SELECT number, name, bot_token, active, chat_id, ip, secret, host, name2 FROM enterprises WHERE number = ?",
        (number,)
    )
    ent = await cur.fetchone()
    if not ent:
        await db.close()
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
    await db.close()

    current_active = ent_dict.get("active", 0)
    new_status = 0 if current_active else 1

    await update_enterprise(
        number,
        ent_dict.get("name", ""),
        ent_dict.get("bot_token", ""),
        ent_dict.get("chat_id", ""),
        ent_dict.get("ip", ""),
        ent_dict.get("secret", ""),
        ent_dict.get("host", ""),
        ent_dict.get("name2", ""),
        active=new_status
    )

    bot_token = ent_dict.get("bot_token", "")
    chat_id = ent_dict.get("chat_id", "")
    bot = Bot(token=bot_token)
    text = f"✅ Сервис {'активирован' if new_status else 'деактивирован'}"
    try:
        await bot.send_message(chat_id=int(chat_id), text=text)
        logger.info(f"Sent toggle message to bot {number}: {text}")
    except TelegramError as e:
        logger.error(f"Toggle bot notification failed: {e}")

    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


# --- Новые эндпоинты для управления сервисами ---

@router.post("/service/restart_main")
async def restart_main_service():
    try:
        subprocess.run(["pkill", "-f", "uvicorn main:app"], check=False)
        await asyncio.sleep(1)
        subprocess.Popen(["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--log-level", "debug", "--reload"])
        return {"detail": "Основной сервис перезапущен"}
    except Exception as e:
        logger.error(f"Ошибка при перезапуске основного сервиса: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось перезапустить основной сервис")


@router.post("/service/restart_all")
async def restart_all_services():
    try:
        subprocess.run(["pkill", "-f", "python"], check=False)
        await asyncio.sleep(2)
        subprocess.Popen(["./start_all.sh"])
        return {"detail": "Все сервисы перезапущены"}
    except Exception as e:
        logger.error(f"Ошибка при полной перезагрузке сервисов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось перезапустить все сервисы")


@router.post("/service/restart_bots")
async def restart_bots_service():
    try:
        subprocess.run(["pkill", "-f", "bot.py"], check=False)
        await asyncio.sleep(1)
        subprocess.Popen(["./start_bots.sh"])
        return {"detail": "Сервисы ботов перезапущены"}
    except Exception as e:
        logger.error(f"Ошибка при перезапуске ботов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось перезапустить ботов")


@router.post("/service/stop_bots")
async def stop_bots_service():
    try:
        subprocess.run(["pkill", "-f", "bot.py"], check=False)
        return {"detail": "Сервисы ботов остановлены"}
    except Exception as e:
        logger.error(f"Ошибка при остановке ботов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось остановить сервисы ботов")


@router.post("/service/toggle_bots")
async def toggle_bots_service():
    try:
        result = subprocess.run(["pgrep", "-fl", "bot.py"], capture_output=True, text=True)
        running = bool(result.stdout.strip())
        if running:
            subprocess.run(["pkill", "-f", "bot.py"], check=False)
            await asyncio.sleep(1)
            detail = "Сервисы ботов остановлены"
        else:
            subprocess.Popen(["./start_bots.sh"])
            detail = "Сервисы ботов запущены"
        return {"detail": detail, "running": not running}
    except Exception as e:
        logger.error(f"Ошибка при переключении ботов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось переключить сервисы ботов")


@router.get("/service/bots_status")
async def bots_status():
    try:
        result = subprocess.run(["pgrep", "-fl", "bot.py"], capture_output=True, text=True)
        running = bool(result.stdout.strip())
        return {"running": running}
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса ботов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось получить статус ботов")


@router.get("/admin")
async def admin_root():
    return RedirectResponse(url="/admin/enterprises")
