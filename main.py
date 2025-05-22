import logging
import asyncio
import contextlib
from fastapi import FastAPI, Request, Form, HTTPException, status, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.logger import logger as fastapi_logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.database import (
    get_all_enterprises,
    get_enterprise_by_number,
    add_enterprise,
    update_enterprise,
    delete_enterprise,
)
from app.services.enterprise import send_message_to_bot
from app.services.bot_status import check_bot_status
from app.services.db import get_all_bot_tokens

from telegram import Bot
from telegram.error import TelegramError

from aiogram import Bot as AiogramBot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.client.default import DefaultBotProperties

# ────────────────────────────────────────────────────────────────────────────────
# Подключаем админ-маршруты
# ────────────────────────────────────────────────────────────────────────────────
from app.routers import admin           # /admin/*
from app.routers.email_users import router as email_users_router   # /admin/email-users
from app.routers.auth_email import router as auth_email_router     # /verify-email/{token}

# Импортируем dispatcher с логикой /start и e-mail
from app.telegram.dispatcher import setup_dispatcher

# Обработчики Asterisk
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup
)
import aiosqlite
from app.config import DB_PATH

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# --- Создаём FastAPI с debug=True для расширенного логирования ---
app = FastAPI(debug=True)

# Повышаем уровень логирования для uvicorn и fastapi
logging.getLogger("uvicorn").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.error").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.access").setLevel(logging.DEBUG)
fastapi_logger.setLevel(logging.DEBUG)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ────────────────────────────────────────────────────────────────────────────────
# Регистрируем роутеры
# ────────────────────────────────────────────────────────────────────────────────
app.include_router(admin.router)              # /admin/*
app.include_router(email_users_router)        # /admin/email-users*
app.include_router(auth_email_router)         # /verify-email/{token}

# --- Обработчик ошибок валидации запросов (422) ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    fastapi_logger.error(
        f"Validation error for {request.method} {request.url}\nErrors: {exc.errors()}"
    )
    try:
        body = await request.body()
        fastapi_logger.debug(f"Request body: {body.decode('utf-8')}")
    except Exception as e:
        fastapi_logger.debug(f"Could not read request body: {e}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger.info("Incoming request: %s %s", request.method, request.url)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Exception during request processing")
            raise
        logger.info("Response status: %d for %s %s", response.status_code, request.method, request.url)
        return response

app.add_middleware(LoggingMiddleware)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/admin/enterprises")

@app.get("/admin/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    logger.info("list_enterprises called")
    enterprises_rows = await get_all_enterprises()
    enterprises = [dict(ent) for ent in enterprises_rows]
    enterprises_sorted = sorted(enterprises, key=lambda e: int(e['number']))

    for ent in enterprises_sorted:
        bot_token = ent.get("bot_token") or ""
        if not bot_token.strip():
            ent["bot_available"] = False
            logger.info(f"Enterprise #{ent['number']} - no bot_token, bot_available set to False")
            continue
        try:
            ent["bot_available"] = await check_bot_status(bot_token)
            logger.info(f"Enterprise #{ent['number']} - bot_available: {ent['bot_available']}")
        except Exception as e:
            logger.error(f"Error checking bot status for #{ent['number']}: {e}")
            ent["bot_available"] = False

    return templates.TemplateResponse(
        "enterprises.html",
        {
            "request": request,
            "enterprises": enterprises_sorted,
            "bots_running": True,
        }
    )

@app.api_route("/admin/enterprises/add", methods=["GET", "POST"], response_class=HTMLResponse)
async def add_enterprise_form(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse(
            "enterprise_form.html",
            {"request": request, "enterprise": {}, "action": "add", "error": None}
        )

    form = await request.form()
    number = form.get("number", "")
    name = form.get("name", "")
    secret = form.get("secret", "")
    bot_token = form.get("bot_token", "")
    chat_id = form.get("chat_id", "")
    ip = form.get("ip", "")
    host = form.get("host", "")
    name2 = form.get("name2", "")

    enterprises = await get_all_enterprises()
    for ent in enterprises:
        if ent['number'] == number:
            error = f"Предприятие с номером {number} уже существует"
            break
        if ent['name'].strip().lower() == name.strip().lower():
            error = f"Предприятие с названием '{name}' уже существует"
            break
        existing_name2 = ent['name2'] or ""
        if existing_name2.strip().lower() == name2.strip().lower() and name2.strip():
            error = f"Предприятие с дополнительным именем '{name2}' уже существует"
            break
        if ent['ip'] == ip:
            error = f"Предприятие с IP {ip} уже существует"
            break
    else:
        error = None

    if error:
        return templates.TemplateResponse(
            "enterprise_form.html",
            {
                "request": request,
                "enterprise": {
                    "number": number,
                    "name": name,
                    "secret": secret,
                    "bot_token": bot_token,
                    "chat_id": chat_id,
                    "ip": ip,
                    "host": host,
                    "name2": name2,
                },
                "action": "add",
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await add_enterprise(number, name, bot_token, chat_id, ip, secret, host, name2)
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_form(request: Request, number: str):
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "enterprise": enterprise, "action": "edit", "error": None}
    )

@app.post("/admin/enterprises/{number}/edit", response_class=HTMLResponse)
async def update_enterprise_post(
    request: Request,
    number: str,
    name: str = Form(...),
    secret: str = Form(...),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    ip: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    enterprises = await get_all_enterprises()
    for ent in enterprises:
        if ent['number'] != number:
            if ent['name'].strip().lower() == name.strip().lower():
                error = f"Предприятие с названием '{name}' уже существует"
                break
            existing_name2 = ent['name2'] or ""
            if existing_name2.strip().lower() == name2.strip().lower() and name2.strip():
                error = f"Предприятие с дополнительным именем '{name2}' уже существует"
                break
            if ent['ip'] == ip:
                error = f"Предприятие с IP {ip} already exists"
                break
    else:
        error = None

    if error:
        enterprise = {
            "number": number,
            "name": name,
            "secret": secret,
            "bot_token": bot_token,
            "chat_id": chat_id,
            "ip": ip,
            "host": host,
            "name2": name2,
        }
        return templates.TemplateResponse(
            "enterprise_form.html",
            {
                "request": request,
                "enterprise": enterprise,
                "action": "edit",
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await update_enterprise(number, name, bot_token, chat_id, ip, secret, host, name2)
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

@app.delete("/admin/enterprises/{number}")
async def delete_enterprise_api(number: str):
    await delete_enterprise(number)
    return {"detail": "Предприятие удалено"}

@app.post("/admin/enterprises/{number}/send_message")
async def send_message_api(number: str, request: Request):
    data = await request.json()
    message = data.get("message")
    logger.debug(f"send_message_api called for enterprise #{number} with message: {message!r}")

    if not message:
        logger.warning(f"Empty message received for enterprise #{number}")
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    enterprise = await get_enterprise_by_number(number)
    logger.debug(f"Enterprise data retrieved for #{number}: {enterprise}")

    if not enterprise:
        logger.error(f"Enterprise #{number} not found in database")
        raise HTTPException(status_code=404, detail="Предприятие не найдено")

    if not isinstance(enterprise, dict):
        enterprise = dict(enterprise)

    bot_token = enterprise.get('bot_token', "")
    chat_id = enterprise.get('chat_id', "")

    if not bot_token.strip():
        logger.error(f"Enterprise #{number} has no bot_token or it is empty")
        raise HTTPException(status_code=400, detail="У предприятия отсутствует токен бота")

    if not chat_id.strip():
        logger.error(f"Enterprise #{number} has no chat_id или оно пустое")
        raise HTTPException(status_code=400, detail="У предприятия отсутствует chat_id для отправки")

    try:
        success = await send_message_to_bot(bot_token, chat_id, message)
        if success:
            logger.info(f"Message sent successfully to enterprise #{number}")
        else:
            logger.error(f"send_message_to_bot returned False for enterprise #{number}")
            raise HTTPException(status_code=500, detail="Не удалось отправить сообщение боту")
    except Exception as e:
        logger.exception(f"Failed to send message to bot {number}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение")

    return {"detail": "Сообщение отправлено"}

@app.post("/admin/enterprises/{number}/toggle")
async def toggle_enterprise(request: Request, number: str):
    logger.info(f"toggle_enterprise called for #{number}")
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        logger.error(f"Enterprise #{number} not found on toggle")
        raise HTTPException(status_code=404, detail="Предприятие не найдено")

    if not isinstance(enterprise, dict):
        enterprise = dict(enterprise)

    current_active = enterprise.get("active", 0)
    new_status = 0 if current_active else 1
    logger.debug(f"Enterprise #{number} current_active={current_active}, toggling to {new_status}")

    await update_enterprise(
        number,
        enterprise.get("name", ""),
        enterprise.get("bot_token", ""),
        enterprise.get("chat_id", ""),
        enterprise.get("ip", ""),
        enterprise.get("secret", ""),
        enterprise.get("host", ""),
        enterprise.get("name2", ""),
        active=new_status
    )

    bot = Bot(token=enterprise.get("bot_token"))
    text = f"✅ Сервис {'активирован' if new_status else 'деактивирован'}"
    try:
        await bot.send_message(chat_id=int(enterprise.get("chat_id")), text=text)
    except TelegramError:
        logger.error("Toggle notification failed for %s", number)

    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

# ────────────────────────────────────────────────────────────────────────────────
# Asterisk Webhooks: рассылаем события всем approved-пользователям по bot_token
# ────────────────────────────────────────────────────────────────────────────────

async def _get_bot_and_recipients(asterisk_token: str) -> tuple[str, list[int]]:
    """
    По Asterisk-Token (поле name2 в enterprises) возвращает:
      - bot_token для Telegram
      - список всех verified tg_id из telegram_users (связь по bot_token)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 1) Получаем bot_token у предприятия по name2
        cur = await db.execute(
            "SELECT bot_token FROM enterprises WHERE name2 = ?",
            (asterisk_token,)
        )
        ent = await cur.fetchone()
        if not ent:
            raise HTTPException(status_code=404, detail="Unknown enterprise token")
        bot_token = ent["bot_token"]

        # 2) Выбираем всех пользователей из telegram_users, у которых тот же bot_token и verified=1
        cur = await db.execute(
            "SELECT tg_id FROM telegram_users WHERE bot_token = ? AND verified = 1",
            (bot_token,)
        )
        rows = await cur.fetchall()

    # Преобразуем tg_id в int
    tg_ids = [int(r["tg_id"]) for r in rows]
    return bot_token, tg_ids



async def _dispatch_to_all(
    handler,  # process_start / process_dial / process_bridge / process_hangup
    body: dict
):
    token = body.get("Token")
    bot_token, tg_ids = await _get_bot_and_recipients(token)
    bot = Bot(token=bot_token)
    results = []

    for chat_id in tg_ids:
        try:
            await handler(bot, chat_id, body)
            results.append({"chat_id": chat_id, "status": "ok"})
        except Exception as e:
            logger.error(f"Asterisk dispatch to {chat_id} failed: {e}")
            results.append({"chat_id": chat_id, "status": "error", "error": str(e)})
    return {"delivered": results}


@app.post("/start")
async def asterisk_start(body: dict = Body(...)):
    return JSONResponse(await _dispatch_to_all(process_start, body))

@app.post("/dial")
async def asterisk_dial(body: dict = Body(...)):
    return JSONResponse(await _dispatch_to_all(process_dial, body))

@app.post("/bridge")
async def asterisk_bridge(body: dict = Body(...)):
    return JSONResponse(await _dispatch_to_all(process_bridge, body))

@app.post("/hangup")
async def asterisk_hangup(body: dict = Body(...)):
    return JSONResponse(await _dispatch_to_all(process_hangup, body))


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down bots gracefully...")
    for task in asyncio.all_tasks():
        task.cancel()
