# app/routers/enterprise.py
# -*- coding: utf-8 -*-

import logging
import os
from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import sys

from app.services.database import (
    get_all_enterprises,
    get_enterprise_by_number,
    add_enterprise,
    update_enterprise,
    delete_enterprise,
)
from app.services.enterprise import send_message_to_bot
from app.services.bot_status import check_bot_status
from app.services.postgres import (
    update_enterprise as postgres_update_enterprise,
    get_enterprise_by_number
)

# Настройка логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(console_handler)

router = APIRouter(prefix="/enterprises", tags=["enterprises"])
templates = Jinja2Templates(directory="app/templates")

@router.get("", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    """
    Список всех предприятий с их статусом (доступен ли бот).
    """
    rows = await get_all_enterprises()
    enterprises = [dict(r) for r in rows]
    # проверяем доступность каждого бота
    for ent in enterprises:
        token = ent.get("bot_token") or ""
        try:
            ent["bot_available"] = await check_bot_status(token) if token.strip() else False
        except Exception:
            ent["bot_available"] = False

    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": enterprises}
    )


@router.get("/add", response_class=HTMLResponse)
async def add_form(request: Request):
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "add", "enterprise": {}}
    )


@router.post("/add", response_class=RedirectResponse)
async def add(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form("")
):
    # Проверка дубликатов
    exists = await get_enterprise_by_number(number)
    if exists:
        raise HTTPException(status_code=400, detail="Номер уже существует")
    await add_enterprise(number, name, bot_token, chat_id, ip, secret, host, name2)
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{number}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, number: str):
    ent = await get_enterprise_by_number(number)
    if not ent:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "edit", "enterprise": dict(ent) if ent else {}}
    )


@router.post("/{number}/edit", response_class=RedirectResponse)
async def edit(
    request: Request,
    number: str,
    name: str = Form(...),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    print(f"EDIT: Начало обновления предприятия {number}", file=sys.stderr, flush=True)
    print(f"EDIT: Параметры: name={name}, ip={ip}, host={host}, name2={name2}", file=sys.stderr, flush=True)
    
    ent = await get_enterprise_by_number(number)
    if not ent:
        print(f"EDIT: Предприятие {number} не найдено", file=sys.stderr, flush=True)
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    
    try:
        print("EDIT: Вызываем postgres_update_enterprise", file=sys.stderr, flush=True)
        await postgres_update_enterprise(
            number=number,
            name=name,
            bot_token=bot_token,
            chat_id=chat_id,
            ip=ip,
            secret=secret,
            host=host,
            name2=name2
        )
        print(f"EDIT: Предприятие {number} успешно обновлено", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"EDIT ERROR: {str(e)}", file=sys.stderr, flush=True)
        # Также передаем ошибку в HTTPException для отображения пользователю, если необходимо
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}") 
    
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{number}", response_class=JSONResponse)
async def delete(number: str):
    await delete_enterprise(number)
    return JSONResponse({"detail": "Предприятие удалено"})


@router.post("/{number}/send_message", response_class=JSONResponse)
async def send_message(number: str, request: Request):
    data = await request.json()
    message = data.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    ent = await get_enterprise_by_number(number)
    if not ent:
        raise HTTPException(status_code=404, detail="Enterprise not found")

    success = await send_message_to_bot(ent["bot_token"], ent["chat_id"], message)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send message")
    return JSONResponse({"detail": "Message sent"})
