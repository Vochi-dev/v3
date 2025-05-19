import logging
import asyncio
import contextlib
from fastapi import FastAPI, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
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
from telegram import Bot
from telegram.error import TelegramError

from aiogram import Bot as AiogramBot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.client.default import DefaultBotProperties

# 👇 ВОССТАНОВЛЕНО: Подключение маршрутов /admin/*
from app.routers import admin

app = FastAPI()
app.include_router(admin.router, prefix="/admin")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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
        existing_name2 = ent['name2'] if ent['name2'] else ""
        if existing_name2.strip().lower() == name2.strip().lower() and name2.strip() != "":
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
            existing_name2 = ent['name2'] if ent['name2'] else ""
            if existing_name2.strip().lower() == name2.strip().lower() and name2.strip() != "":
                error = f"Предприятие с дополнительным именем '{name2}' уже существует"
                break
            if ent['ip'] == ip:
                error = f"Предприятие с IP {ip} уже существует"
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

    logger.debug(f"Using bot_token={bot_token!r}, chat_id={chat_id!r} for enterprise #{number}")

    if not bot_token or not bot_token.strip():
        logger.error(f"Enterprise #{number} has no bot_token or it is empty")
        raise HTTPException(status_code=400, detail="У предприятия отсутствует токен бота")

    if not chat_id or not chat_id.strip():
        logger.error(f"Enterprise #{number} has no chat_id or it is empty")
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

    bot_token = enterprise.get("bot_token", "")
    chat_id = enterprise.get("chat_id", "")
    bot = Bot(token=bot_token)
    text = f"✅ Сервис {'активирован' if new_status else 'деактивирован'}"
    try:
        await bot.send_message(chat_id=int(chat_id), text=text)
        logger.info(f"Sent toggle message to bot {number}: {text}")
    except TelegramError as e:
        logger.error(f"Toggle bot notification failed for #{number}: {e}")

    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


BOT_TOKENS = {
    "0100": "TOKEN1",
    "0201": "TOKEN2",
    "0262": "TOKEN3",
}


async def start_bot(enterprise_number: str):
    token = BOT_TOKENS.get(enterprise_number)
    if not token:
        logger.error(f"No bot token for enterprise {enterprise_number}")
        return
    bot = AiogramBot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    @dp.message(Command(commands=["start"]))
    async def start_handler(message: types.Message):
        await message.answer(f"Привет! Бот предприятия {enterprise_number} запущен и готов к работе.")

    try:
        logger.info(f"Starting bot for enterprise {enterprise_number}")
        await dp.start_polling(bot)
    except TelegramAPIError as e:
        logger.error(f"Telegram API error on bot {enterprise_number}: {e}")
    finally:
        await bot.session.close()


async def start_all_bots():
    tasks = []
    for ent_num in BOT_TOKENS.keys():
        tasks.append(asyncio.create_task(start_bot(ent_num)))
    await asyncio.gather(*tasks)


@app.on_event("startup")
async def on_startup():
    logger.info("Starting all telegram bots in background task...")
    asyncio.create_task(start_all_bots())


@app.get("/admin")
async def admin_root():
    return RedirectResponse(url="/admin/enterprises")


@app.get("/service/bots_status")
async def bots_status():
    return {"running": True}


@app.post("/service/toggle_bots")
async def toggle_bots_service():
    logger.info("toggle_bots_service called - пока не реализовано")
    return {"detail": "Сервис переключения ботов не реализован"}


if __name__ == "__main__":
    import uvicorn
    logger.info("Запускаем FastAPI + Telegram ботов одной командой")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, log_level="debug")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down bots gracefully...")
    for task in asyncio.all_tasks():
        task.cancel()
