# app/routers/email_users.py
# -*- coding: utf-8 -*-

import csv
import io
import base64
import logging
from typing import List, Dict

import aiosqlite
from fastapi import (
    APIRouter, Request, status, HTTPException,
    UploadFile, File, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from telegram import Bot
from telegram.error import TelegramError

from app.config import DB_PATH
from app.routers.admin import require_login
from app.services.db import get_connection

router = APIRouter(prefix="/admin/email-users", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

logger = logging.getLogger("email_users")
logger.setLevel(logging.DEBUG)


@router.get("", response_class=HTMLResponse)
async def list_email_users(request: Request):
    require_login(request)
    db = await get_connection()
    db.row_factory = lambda c, r: {c.description[i][0]: r[i] for i in range(len(r))}
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
        logger.debug("Executing SQL for list_email_users: %s", sql.strip())
        cur = await db.execute(sql)
        rows = await cur.fetchall()
        logger.debug("Fetched %d rows from email_users", len(rows))
    finally:
        await db.close()

    return templates.TemplateResponse(
        "email_users.html",
        {"request": request, "email_users": rows}
    )


# ... ваши upload / confirm handlers без изменений ...


@router.post("/delete/{tg_id}", response_class=RedirectResponse)
async def delete_user(tg_id: int, request: Request):
    require_login(request)

    bot_token = None

    # 1. Сначала пробуем найти bot_token в telegram_users
    async with aiosqlite.connect(DB_PATH) as db1:
        db1.row_factory = aiosqlite.Row
        cur1 = await db1.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?",
            (tg_id,)
        )
        row1 = await cur1.fetchone()
        await cur1.close()
    if row1 and row1["bot_token"]:
        bot_token = row1["bot_token"]
        logger.info(f"[delete] bot_token найден в telegram_users: {bot_token}")

    # 2. Если не нашли — пробуем enterprise_users
    if not bot_token:
        async with aiosqlite.connect(DB_PATH) as db2:
            db2.row_factory = aiosqlite.Row
            cur2 = await db2.execute("""
                SELECT e.bot_token
                  FROM enterprise_users u
                  JOIN enterprises e ON u.enterprise_id = e.number
                 WHERE u.telegram_id = ?
                   AND u.status = 'approved'
            """, (tg_id,))
            row2 = await cur2.fetchone()
            await cur2.close()
        if row2 and row2["bot_token"]:
            bot_token = row2["bot_token"]
            logger.info(f"[delete] bot_token найден в enterprise_users: {bot_token}")

    # 3. Если нашли токен — отправляем уведомление
    if bot_token:
        bot = Bot(token=bot_token)
        try:
            logger.info(f"[delete] Отправка уведомления об удалении пользователю {tg_id} через бот {bot_token}")
            bot.send_message(chat_id=tg_id,
                             text="❌ Ваш доступ к боту был отозван администратором.")
            logger.info(f"[delete] Уведомление отправлено пользователю {tg_id}")
        except TelegramError as e:
            logger.warning(f"[delete] Ошибка при отправке уведомления пользователю {tg_id}: {e}")
        finally:
            # Закрываем HTTP-сессию python-telegram-bot
            await bot.session.close()
    else:
        logger.error(f"[delete] bot_token для пользователя {tg_id} не найден — уведомление не отправлено")

    # 4. Удаляем записи из БД
    db = await get_connection()
    try:
        await db.execute("DELETE FROM telegram_users WHERE tg_id = ?", (tg_id,))
        await db.execute("DELETE FROM enterprise_users WHERE telegram_id = ?", (tg_id,))
        await db.commit()
        logger.info(f"[delete] Пользователь {tg_id} удалён из БД")
    finally:
        await db.close()

    return RedirectResponse("/admin/email-users", status_code=status.HTTP_303_SEE_OTHER)
