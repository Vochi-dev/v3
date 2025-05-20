# app/routers/email_users.py
# -*- coding: utf-8 -*-

import logging

import aiosqlite
from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.db import get_connection
from app.services.enterprise import send_message_to_bot

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

logger = logging.getLogger("email_users")
logger.setLevel(logging.DEBUG)


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    """
    Вывод списка email-пользователей с привязкой к Telegram.
    """
    require_login(request)
    db = await get_connection()
    # Каждая строка как dict
    db.row_factory = lambda cursor, row: {cursor.description[i][0]: row[i] for i in range(len(row))}
    try:
        sql = """
            SELECT
              eu.number               AS number,
              eu.email                AS email,
              eu.name                 AS name,
              eu.right_all            AS right_all,
              eu.right_1              AS right_1,
              eu.right_2              AS right_2,
              tu.tg_id                AS tg_id,
              COALESCE(ent_app.name, ent_bot.name, '') AS enterprise_name
            FROM email_users eu
            LEFT JOIN telegram_users tu
              ON tu.email = eu.email
            LEFT JOIN enterprise_users ue_app
              ON ue_app.telegram_id = tu.tg_id
              AND ue_app.status = 'approved'
            LEFT JOIN enterprises ent_app
              ON ent_app.number = ue_app.enterprise_id
            LEFT JOIN enterprises ent_bot
              ON ent_bot.bot_token = tu.bot_token
            ORDER BY eu.number, eu.email
        """
        cur = await db.execute(sql)
        rows = await cur.fetchall()
    finally:
        await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows}
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_email_users(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Загрузка CSV и подготовка к синхронизации:
    формируем список to_remove и показываем confirm_sync.html.
    """
    # ... здесь код, как было раньше ...


@router.post("/upload/confirm", response_class=RedirectResponse)
async def confirm_upload(
    request: Request,
    csv_b64: str = Form(...),
    confirm: str   = Form(...),
):
    """
    Подтверждение синхронизации CSV: удаляем/добавляем email_users.
    """
    # ... здесь код, как было раньше ...


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    """
    По нажатию Delete только отправляем фиксированное сообщение,
    не трогаем базу.
    """
    require_login(request)

    # 1) Находим bot_token из telegram_users
    bot_token = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?",
            (tg_id,)
        )
        row = await cur.fetchone()

    if not row or not row["bot_token"]:
        logger.error(f"[delete] bot_token не найден для tg_id={tg_id}, сообщение не отправлено")
        return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)

    bot_token = row["bot_token"]
    logger.info(f"[delete] Найден bot_token={bot_token} для tg_id={tg_id}")

    # 2) Отправляем единственное фиксированное сообщение
    try:
        sent = await send_message_to_bot(
            bot_token,
            tg_id,
            "❌ Ваш доступ к боту был отозван администратором."
        )
        if sent:
            logger.info(f"[delete] Уведомление отправлено пользователю {tg_id}")
        else:
            logger.warning(f"[delete] Не удалось отправить уведомление пользователю {tg_id}")
    except Exception as e:
        logger.error(f"[delete] Ошибка при отправке уведомления пользователю {tg_id}: {e}")

    # 3) Больше ничего не делаем — **не изменяем базу**
    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)
