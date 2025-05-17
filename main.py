import logging
import asyncio

from fastapi import FastAPI, Request, Depends, Form, HTTPException, status
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
    # Преобразуем sqlite3.Row в dict для удобства
    enterprises = [dict(ent) for ent in enterprises_rows]

    enterprises_sorted = sorted(enterprises, key=lambda e: int(e['number']))

    for ent in enterprises_sorted:
        try:
            ent["bot_available"] = await check_bot_status(ent["bot_token"])
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
        {"request": request, "enterprise": None, "is_new": True}
    )


@app.post("/admin/enterprises/new")
async def create_enterprise(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    secret: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    host: str = Form(...),
):
    existing = await get_enterprise_by_number(number)
    if existing:
        return templates.TemplateResponse(
            "enterprise_form.html",
            {
                "request": request,
                "enterprise": {"number": number, "name": name, "secret": secret,
                               "bot_token": bot_token, "chat_id": chat_id, "ip": ip, "host": host},
                "is_new": True,
                "error": "Предприятие с таким номером уже существует"
            }
        )
    await add_enterprise(number, name, bot_token, chat_id, ip, secret, host)
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
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
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
    if not message:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    bot_token = enterprise['bot_token']
    chat_id = enterprise['chat_id']

    success = await send_message_to_bot(bot_token, chat_id, message)
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение боту")
    return {"detail": "Сообщение отправлено"}


@app.post("/admin/enterprises/{number}/toggle")
async def toggle_enterprise(request: Request, number: str):
    """
    Переключение активности предприятия (active 0/1) и уведомление бота
    """
    enterprise = await get_enterprise_by_number(number)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")

    # Приводим к dict, если ещё не dict
    if not isinstance(enterprise, dict):
        enterprise = dict(enterprise)

    current_active = enterprise.get("active", 0)
    new_status = 0 if current_active else 1

    # Вызов update_enterprise с параметром active
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

    # Отправка уведомления в Telegram
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


@app.get("/admin")
async def admin_root():
    return RedirectResponse(url="/admin/enterprises")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, log_level="info", reload=True)
