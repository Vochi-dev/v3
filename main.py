dimport logging
import asyncio
import subprocess
from fastapi import FastAPI, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

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

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
@app.on_event("startup")
async def startup_event():
    try:
        import subprocess
        subprocess.Popen(["./start_bots.sh"])
        logger.info("✅ Боты запущены при старте сервера.")
    except Exception as e:
        logger.error(f"Ошибка запуска ботов при старте: {e}")




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
        {"request": request, "enterprises": enterprises_sorted}
    )


@app.get("/admin/enterprises/new", response_class=HTMLResponse)
async def new_enterprise_form(request: Request):
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "enterprise": {}, "action": "add", "error": None}
    )


@app.post("/admin/enterprises/new", response_class=HTMLResponse)
async def create_enterprise(
    request: Request,
    number: str = Form(...),
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


# --- Алиасы для совместимости с /admin/enterprises/add ---

@app.get("/admin/enterprises/add", response_class=HTMLResponse)
async def add_enterprise_form_alias(request: Request):
    return await new_enterprise_form(request)


@app.post("/admin/enterprises/add", response_class=HTMLResponse)
async def create_enterprise_alias(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    secret: str = Form(...),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    ip: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    return await create_enterprise(
        request=request,
        number=number,
        name=name,
        secret=secret,
        bot_token=bot_token,
        chat_id=chat_id,
        ip=ip,
        host=host,
        name2=name2,
    )


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
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")

    if not isinstance(enterprise, dict):
        enterprise = dict(enterprise)

    current_active = enterprise.get("active", 0)
    new_status = 0 if current_active else 1

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
        logger.error(f"Toggle bot notification failed: {e}")

    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/service/restart_main")
async def restart_main_service():
    try:
        subprocess.run(["pkill", "-f", "uvicorn main:app"], check=False)
        await asyncio.sleep(1)
        subprocess.Popen(["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--log-level", "debug", "--reload"])
        return {"detail": "Основной сервис перезапущен"}
    except Exception as e:
        logger.error(f"Ошибка при перезапуске основного сервиса: {e}")
        raise HTTPException(status_code=500, detail="Не удалось перезапустить основной сервис")


@app.post("/service/restart_all")
async def restart_all_services():
    try:
        subprocess.run(["pkill", "-f", "python"], check=False)
        await asyncio.sleep(2)
        subprocess.Popen(["./start_all.sh"])
        return {"detail": "Все сервисы перезапущены"}
    except Exception as e:
        logger.error(f"Ошибка при полной перезагрузке сервисов: {e}")
        raise HTTPException(status_code=500, detail="Не удалось перезапустить все сервисы")


@app.post("/service/restart_bots")
async def restart_bots_service():
    try:
        subprocess.run(["pkill", "-f", "bot.py"], check=False)
        await asyncio.sleep(1)
        subprocess.Popen(["./start_bots.sh"])
        return {"detail": "Сервисы ботов перезапущены"}
    except Exception as e:
        logger.error(f"Ошибка при перезапуске ботов: {e}")
        raise HTTPException(status_code=500, detail="Не удалось перезапустить ботов")


@app.post("/service/stop_bots")
async def stop_bots_service():
    try:
        subprocess.run(["pkill", "-f", "bot.py"], check=False)
        return {"detail": "Сервисы ботов остановлены"}
    except Exception as e:
        logger.error(f"Ошибка при остановке ботов: {e}")
        raise HTTPException(status_code=500, detail="Не удалось остановить сервисы ботов")


@app.post("/service/toggle_bots")
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
        logger.error(f"Ошибка при переключении ботов: {e}")
        raise HTTPException(status_code=500, detail="Не удалось переключить сервисы ботов")







@app.get("/admin")
async def admin_root():
    return RedirectResponse(url="/admin/enterprises")
