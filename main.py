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
    process_hangup,
    create_resend_loop,
    dial_cache,
    bridge_store,
    active_bridges,
)
import aiosqlite
from app.config import DB_PATH

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.error").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.access").setLevel(logging.DEBUG)
fastapi_logger.setLevel(logging.DEBUG)

# --- Создаём FastAPI с debug=True для расширенного логирования ---
app = FastAPI(debug=True)

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
        ent_data = {
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
                "enterprise": ent_data,
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
    if not enterprise:
        logger.error(f"Enterprise #{number} not found")
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    if not isinstance(enterprise, dict):
        enterprise = dict(enterprise)

    bot_token = enterprise.get("bot_token", "")
    chat_id = enterprise.get("chat_id", "")
    if not bot_token.strip():
        raise HTTPException(status_code=400, detail="У предприятия отсутствует токен бота")
    if not chat_id.strip():
        raise HTTPException(status_code=400, detail="У предприятия отсутствует chat_id")

    try:
        success = await send_message_to_bot(bot_token, chat_id, message)
        if not success:
            raise HTTPException(status_code=500, detail="Не удалось отправить сообщение боту")
    except Exception as e:
        logger.exception(f"Failed to send message: {e}")
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение")

    return {"detail": "Сообщение отправлено"}

@app.post("/admin/enterprises/{number}/toggle")
async def toggle_enterprise(request: Request, number: str):
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    if not isinstance(enterprise, dict):
        enterprise = dict(enterprise)

    current = enterprise.get("active", 0)
    new = 0 if current else 1
    await update_enterprise(
        number,
        enterprise["name"],
        enterprise["bot_token"],
        enterprise["chat_id"],
        enterprise["ip"],
        enterprise["secret"],
        enterprise["host"],
        enterprise["name2"],
        active=new
    )
    bot = Bot(token=enterprise["bot_token"])
    text = f"✅ Сервис {'активирован' if new else 'деактивирован'}"
    try:
        await bot.send_message(chat_id=int(enterprise["chat_id"]), text=text)
    except TelegramError:
        pass
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

# ────────────────────────────────────────────────────────────────────────────────
# Asterisk Webhooks: рассылка всем approved
# ────────────────────────────────────────────────────────────────────────────────
async def _get_bot_and_recipients(asterisk_token: str) -> tuple[str,int,list[int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT number, bot_token, chat_id FROM enterprises WHERE name2 = ?",
            (asterisk_token,),
        )
        ent = await cur.fetchone()
        if not ent:
            raise HTTPException(status_code=404, detail="Unknown enterprise token")
        token, admin_chat = ent["bot_token"], int(ent["chat_id"])
        cur = await db.execute(
            "SELECT telegram_id FROM enterprise_users WHERE enterprise_id = ? AND status='approved'",
            (ent["number"],),
        )
        rows = await cur.fetchall()
    return token, admin_chat, [int(r["telegram_id"]) for r in rows]

async def _dispatch_to_all(handler, body: dict):
    token, admin_chat, users = await _get_bot_and_recipients(body.get("Token"))
    bot = Bot(token=token)
    results = []
    for uid in users:
        try:
            await handler(bot, uid, body)
            results.append({"chat_id": uid, "status": "ok"})
        except Exception as e:
            logger.error(f"Dispatch to {uid} failed: {e}")
            results.append({"chat_id": uid, "status": "error", "error": str(e)})
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

# ────────────────────────────────────────────────────────────────────────────────
# Запуск ботов и resend loops
# ────────────────────────────────────────────────────────────────────────────────
async def start_bot_with_resend(ent_number: str, token: str, chat_id: int):
    aiobot = AiogramBot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = await setup_dispatcher(aiobot, ent_number)
    # запускаем цикл переотправки активных мостов
    asyncio.create_task(create_resend_loop(dial_cache, bridge_store, active_bridges, aiobot, chat_id))
    try:
        await dp.start_polling(aiobot)
    except TelegramAPIError as e:
        logger.error(f"Bot {ent_number} API error: {e}")
    finally:
        await aiobot.session.close()

@app.on_event("startup")
async def on_startup():
    enterprises = await get_all_enterprises()
    for ent in enterprises:
        tok = ent["bot_token"] or ""
        if not tok.strip():
            continue
        asyncio.create_task(start_bot_with_resend(ent["number"], tok, int(ent["chat_id"])))

@app.on_event("shutdown")
async def on_shutdown():
    for task in asyncio.all_tasks():
        task.cancel()

@app.get("/service/bots_status")
async def bots_status():
    return {"running": True}

@app.post("/service/toggle_bots")
async def toggle_bots_service():
    return {"detail": "Not implemented"}

@app.get("/admin")
async def admin_root():
    return RedirectResponse(url="/admin/enterprises")
