# app/routers/enterprise.py
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.routers.admin import require_login
from app.services.db import get_connection
from app.services.enterprise import get_enterprise, update_enterprise
import datetime as dt

router = APIRouter(prefix="/admin/enterprises", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    require_login(request)
    db = await get_connection()
    try:
        cur = await db.execute(
            "SELECT number, name, bot_token, chat_id, ip, secret, host, created_at, name2 "
            "FROM enterprises ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": rows}
    )


@router.get("/add", response_class=HTMLResponse)
async def add_enterprise_page(request: Request):
    require_login(request)
    return templates.TemplateResponse(
        "add_enterprise.html",
        {"request": request}
    )


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
    return RedirectResponse(
        url="/admin/enterprises",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{number}/edit", response_class=HTMLResponse)
async def edit_enterprise_page(request: Request, number: str):
    require_login(request)
    # читаем из сервиса
    ent = get_enterprise(number)
    if not ent:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    return templates.TemplateResponse(
        "edit_enterprise.html",
        {"request": request, "e": ent}
    )


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
    # вызываем сервис
    update_enterprise(number, name, name2, bot_token, chat_id, ip, secret, host)
    return RedirectResponse(
        url="/admin/enterprises",
        status_code=status.HTTP_303_SEE_OTHER
    )
