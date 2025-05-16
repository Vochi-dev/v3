# app/routers/enterprise.py
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.routers.admin import require_login
from app.services.db import get_connection
from app.services.enterprise import get_enterprise, update_enterprise
from app.config import NOTIFY_BOT_TOKEN, NOTIFY_CHAT_ID
import datetime as dt
from telegram import Bot

router = APIRouter(prefix="/admin/enterprises", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    db.row_factory = lambda cursor, row: {
        col[0]: row[idx] for idx, col in enumerate(cursor.description)
    }
    try:
        cur = await db.execute(
            "SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2, active "
            "FROM enterprises ORDER BY created_at DESC"
        )
        enterprises = await cur.fetchall()

        for ent in enterprises:
            try:
                bot = Bot(token=ent["bot_token"])
                await bot.get_me()
                ent["bot_active"] = True
            except Exception:
                ent["bot_active"] = False
    finally:
        await db.close()

    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": enterprises}
    )


@router.get("/add", response_class=HTMLResponse)
async def add_enterprise_page(request: Request):
    require_login(request)
    return templates.TemplateResponse("add_enterprise.html", {"request": request})


@router.post("/add", response_class=RedirectResponse)
async def add_enterprise(
    request: Request,
    number: str = Form(...),
    name: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
    name2: str = Form(""),
):
    require_login(request)
    created_at = dt.datetime.utcnow().isoformat()
    db = await get_connection()
    try:
        await db.execute(
            "INSERT INTO enterprises (number,name,bot_token,chat_id,ip,secret,host,created_at,name2) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (number, name, bot_token, chat_id, ip, secret, host, created_at, name2),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_page(request: Request, number: str):
    require_login(request)
    ent = get_enterprise(number)
    if not ent:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    return templates.TemplateResponse("edit_enterprise.html", {"request": request, "e": ent})


@router.post("/{number}/edit", response_class=RedirectResponse)
async def edit_enterprise(
    request: Request,
    number: str,
    name: str = Form(...),
    name2: str = Form(""),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(""),
    secret: str = Form(""),
    host: str = Form(""),
):
    require_login(request)
    update_enterprise(number, name, name2, bot_token, chat_id, ip, secret, host)
    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{number}/toggle")
async def toggle_enterprise(request: Request, number: str):
    require_login(request)
    db = await get_connection()
    db.row_factory = lambda cursor, row: {
        col[0]: row[idx] for idx, col in enumerate(cursor.description)
    }
    try:
        cur = await db.execute(
            "SELECT active, bot_token, name FROM enterprises WHERE number = ?",
            (number,),
        )
        row = await cur.fetchone()

        if not row:
            return RedirectResponse("/admin/enterprises", status_code=status.HTTP_302_FOUND)

        new_status = 0 if row["active"] else 1
        await db.execute(
            "UPDATE enterprises SET active = ? WHERE number = ?",
            (new_status, number),
        )
        await db.commit()
    finally:
        await db.close()

    try:
        text = (
            f'🟢 Бот *{row["name"]}* запущен ✅'
            if new_status
            else f'🔴 Бот *{row["name"]}* остановлен ⛔️'
        )
        bot = Bot(token=NOTIFY_BOT_TOKEN)
        await bot.send_message(chat_id=NOTIFY_CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка при уведомлении notify-ботом: {e}")

    return RedirectResponse("/admin/enterprises", status_code=status.HTTP_302_FOUND)
