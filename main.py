import logging

from fastapi import FastAPI, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.services.database import (
    get_enterprises_with_tokens,
    get_enterprise_by_number,
    update_enterprise,
    add_enterprise,
    delete_enterprise,
)
from app.services.enterprise import send_message_to_bot
from app.services.bot_status import check_bot_status

from telegram import Bot
from telegram.error import TelegramError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

templates = Jinja2Templates(directory="app/templates")

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/admin/enterprises")


@app.get("/admin/enterprises", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    logger.info("list_enterprises called")
    enterprises_rows = await get_enterprises_with_tokens()
    enterprises = [dict(ent) for ent in enterprises_rows]

    enterprises_sorted = sorted(enterprises, key=lambda e: int(e['number']))

    for ent in enterprises_sorted:
        try:
            ent["bot_available"] = await check_bot_status(ent.get("bot_token", ""))
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
        {"request": request, "enterprise": {}, "is_new": True}
    )


@app.post("/admin/enterprises/new")
async def create_enterprise(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    secret: str = Form(...),
    bot_token: str = Form(None),
    chat_id: str = Form(None),
    ip: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    enterprises = await get_enterprises_with_tokens()
    for ent in enterprises:
        if (ent['number'] == number or
            ent['name'] == name or
            ent.get('name2', '') == name2 or
            ent['ip'] == ip):
            return templates.TemplateResponse(
                "enterprise_form.html",
                {
                    "request": request,
                    "enterprise": {
                        "number": number,
                        "name": name,
                        "secret": secret,
                        "bot_token": bot_token or "",
                        "chat_id": chat_id or "",
                        "ip": ip,
                        "host": host,
                        "name2": name2,
                    },
                    "is_new": True,
                    "error": "Предприятие с таким номером, названием, доп. именем или IP уже существует"
                }
            )
    await add_enterprise(number, name, bot_token or "", chat_id or "", ip, secret, host, name2)
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/enterprises/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_form(request: Request, number: str):
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "enterprise": enterprise, "is_new": False}
    )


@app.post("/admin/enterprises/{number}/edit")
async def update_enterprise_post(
    request: Request,
    number: str,
    name: str = Form(...),
    secret: str = Form(...),
    bot_token: str = Form(None),
    chat_id: str = Form(None),
    ip: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    await update_enterprise(number, name, bot_token or "", chat_id or "", ip, secret, host, name2)
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@app.delete("/admin/enterprises/{number}")
async def delete_enterprise_api(number: str):
    await delete_enterprise(number)
    return {"detail": "Предприятие удалено"}


@app.post("/admin/enterprises/{number}/send_message")
async def send_message_api(number: str, request: Request):
    data = await request.json()
    message = data.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    bot_token = enterprise.get('bot_token', "")
    chat_id = enterprise.get('chat_id', "")

    success = await send_message_to_bot(bot_token, chat_id, message)
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение боту")
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
    text = f"✅ Сервис {'активирован' if new_status
