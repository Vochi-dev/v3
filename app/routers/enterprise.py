# app/routers/enterprise.py
# -*- coding: utf-8 -*-

import logging
import os
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from fastapi import APIRouter, Request, Form, status, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import asyncpg
from fastapi import Depends
from asyncpg.connection import Connection as AsyncConnection
from sqlalchemy.exc import NoResultFound
from app.services import postgres as db_services
from app.services.postgres import get_db

from app.services.database import get_all_enterprises
from app.services.enterprise import send_message_to_bot
from app.services.bot_status import check_bot_status
from app.services.fail2ban import get_banned_count
from app.services.postgres import (
    add_enterprise,
    update_enterprise as postgres_update_enterprise,
    get_enterprise_by_number,
    get_gateways_by_enterprise_number,
    add_goip_gateway,
    update_goip_gateway,
    delete_goip_gateway,
    get_goip_gateway_by_id,
    create_gsm_lines_for_gateway
)
from pydantic import BaseModel, constr, conint

# --- Настройка ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(console_handler)

CONFIG_BASE_PATH = Path("uploads/gateways")

router = APIRouter(prefix="/admin/enterprises", tags=["enterprises"])
templates = Jinja2Templates(directory="app/templates")

# --- Модели Pydantic ---
class GatewayCreate(BaseModel):
    gateway_name: constr(strip_whitespace=True, min_length=1)
    line_count: conint(ge=1, le=32)
    custom_boolean_flag: bool = False

class GatewayUpdate(BaseModel):
    gateway_name: constr(strip_whitespace=True, min_length=1)
    custom_boolean_flag: bool = False

# --- Эндпоинты для рендеринга HTML ---

@router.get("", response_class=HTMLResponse)
async def list_enterprises(request: Request):
    rows = await get_all_enterprises()
    enterprises = [dict(r) for r in rows]
    banned_count = await get_banned_count()

    for ent in enterprises:
        token = ent.get("bot_token") or ""
        try:
            ent["bot_available"] = await check_bot_status(token) if token.strip() else False
        except Exception:
            ent["bot_available"] = False
    return templates.TemplateResponse(
        "enterprises.html",
        {"request": request, "enterprises": enterprises, "banned_count": banned_count}
    )

@router.get("/add", response_class=HTMLResponse)
async def add_form(request: Request):
    return templates.TemplateResponse(
        "enterprise_form.html",
        {"request": request, "action": "add", "enterprise": {}, "gateways": []}
    )

@router.post("/add", response_class=RedirectResponse)
async def add_enterprise_post(request: Request):
    form_data = await request.form()
    number = form_data.get("number", "")
    try:
        data = {
            "number": number,
            "name": form_data.get("name", ""),
            "bot_token": form_data.get("bot_token", ""),
            "chat_id": form_data.get("chat_id", "374573193"),
            "ip": form_data.get("ip", ""),
            "secret": form_data.get("secret", ""),
            "host": form_data.get("host", ""),
            "name2": form_data.get("name2", ""),
            "is_enabled": form_data.get("is_enabled") == "true",
            "active": form_data.get("active") == "true",
            "scheme_count": int(form_data.get("scheme_count", 3)),
            "gsm_line_count": int(form_data.get("gsm_line_count", 8)),
            "parameter_option_1": form_data.get("parameter_option_1") == "true",
            "parameter_option_2": form_data.get("parameter_option_2") == "true",
            "parameter_option_3": form_data.get("parameter_option_3") == "true",
            "parameter_option_4": form_data.get("parameter_option_4") == "true",
            "parameter_option_5": form_data.get("parameter_option_5") == "true",
            "custom_domain": form_data.get("custom_domain"),
            "custom_port": int(p) if (p := form_data.get("custom_port")) and p.isdigit() else None
        }
        await add_enterprise(**data)
        return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail=f"Ошибка: Номер предприятия '{number}' уже используется.")
    except Exception as e:
        logger.error(f"Ошибка при добавлении предприятия {number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")

@router.get("/{number}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, number: str):
    ent = await get_enterprise_by_number(number)
    if not ent:
        raise HTTPException(status_code=404, detail="Предприятие не найдено")
    
    gateways_list = await get_gateways_by_enterprise_number(number) 
    
    gateways_for_template = []
    if gateways_list:
        for gw_row in gateways_list:
            gw_dict = dict(gw_row)
            if gw_dict.get('config_backup_uploaded_at') and isinstance(gw_dict['config_backup_uploaded_at'], datetime):
                gw_dict['config_backup_uploaded_at'] = gw_dict['config_backup_uploaded_at'].isoformat()
            gateways_for_template.append(gw_dict)

    notification = request.query_params.get('notification')

    return templates.TemplateResponse(
        "enterprise_form.html",
        {
            "request": request,
            "action": "edit",
            "enterprise": ent,
            "gateways": gateways_for_template,
            "notification": notification
        }
    )

@router.post("/{number}/edit", response_class=RedirectResponse)
async def edit_enterprise_post(request: Request, number: str, db: AsyncConnection = Depends(get_db)):
    form_data = await request.form()
    
    # Получаем ID шлюза, который нужно пометить для перезагрузки
    reboot_gateway_id_str = form_data.get("reboot_gateway_id")
    
    # (логика извлечения данных предприятия)
    name = form_data.get("name", "")
    bot_token = form_data.get("bot_token", "")
    chat_id = form_data.get("chat_id", "374573193")
    ip = form_data.get("ip", "")
    secret = form_data.get("secret", "")
    host = form_data.get("host", "")
    name2 = form_data.get("name2", "")
    is_enabled = form_data.get("is_enabled") == "true"
    active = form_data.get("active") == "true"
    scheme_count = int(form_data.get("scheme_count", 3))
    gsm_line_count = int(form_data.get("gsm_line_count", 8))
    parameter_option_1 = form_data.get("parameter_option_1") == "true"
    parameter_option_2 = form_data.get("parameter_option_2") == "true"
    parameter_option_3 = form_data.get("parameter_option_3") == "true"
    parameter_option_4 = form_data.get("parameter_option_4") == "true"
    parameter_option_5 = form_data.get("parameter_option_5") == "true"
    custom_domain = form_data.get("custom_domain")
    custom_port_str = form_data.get("custom_port")
    custom_port = int(custom_port_str) if custom_port_str and custom_port_str.isdigit() else None

    await postgres_update_enterprise(
        number, name, bot_token, chat_id, ip, secret, host, name2, is_enabled, active,
        scheme_count, gsm_line_count, parameter_option_1, parameter_option_2,
        parameter_option_3, parameter_option_4, parameter_option_5,
        custom_domain, custom_port
    )
    
    # Теперь обрабатываем состояние флагов "Reboot" для шлюзов
    if reboot_gateway_id_str:
        all_gateways = await get_gateways_by_enterprise_number(number)
        reboot_gateway_id = int(reboot_gateway_id_str) if reboot_gateway_id_str.isdigit() else None
        
        for gw in all_gateways:
            gw_id = gw['id']
            current_flag_state = gw.get('custom_boolean_flag', False)
            should_have_flag = (gw_id == reboot_gateway_id)

            # Обновляем только если состояние должно измениться
            if current_flag_state != should_have_flag:
                await update_goip_gateway(
                    gateway_id=gw_id,
                    gateway_name=gw['gateway_name'],
                    custom_boolean_flag=should_have_flag
                )
    
    return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/{enterprise_number}/gateways", response_class=JSONResponse, status_code=status.HTTP_201_CREATED)
async def create_gateway_for_enterprise(
    enterprise_number: str,
    gateway_data: GatewayCreate,
    db: AsyncConnection = Depends(get_db)
):
    """
    Создает шлюз и его линии в одной транзакции.
    """
    try:
        async with db.transaction():
            # Шаг 1: Создаем шлюз
            new_gateway = await add_goip_gateway(
                conn=db,
                enterprise_number=enterprise_number,
                gateway_name=gateway_data.gateway_name,
                line_count=gateway_data.line_count,
                custom_boolean_flag=gateway_data.custom_boolean_flag
            )

            # Шаг 2: Создаем линии для этого шлюза
            if new_gateway and gateway_data.line_count > 0:
                await create_gsm_lines_for_gateway(
                    conn=db,
                    gateway_id=new_gateway['id'],
                    gateway_name=new_gateway['gateway_name'],
                    enterprise_number=enterprise_number,
                    line_count=gateway_data.line_count
                )
            
            # Конвертируем datetime для JSON-ответа
            gateway_dict = dict(new_gateway)
            if created_at := gateway_dict.get('created_at'):
                gateway_dict['created_at'] = created_at.isoformat()
            
            return JSONResponse(content=gateway_dict, status_code=status.HTTP_201_CREATED)

    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"Шлюз с именем '{gateway_data.gateway_name}' уже существует.")
    except Exception as e:
        logger.error(f"API-ошибка создания шлюза: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/gateways/{gateway_id}", response_class=JSONResponse)
async def update_gateway_for_enterprise(gateway_id: int, gateway_data: GatewayUpdate):
    try:
        updated_gateway = await update_goip_gateway(
            gateway_id=gateway_id,
            gateway_name=gateway_data.gateway_name,
            custom_boolean_flag=gateway_data.custom_boolean_flag
        )
        if not updated_gateway:
            raise HTTPException(status_code=404, detail="Шлюз не найден")
        
        # Конвертируем datetime для JSON-ответа
        gateway_dict = dict(updated_gateway)
        for key, value in gateway_dict.items():
            if isinstance(value, datetime):
                gateway_dict[key] = value.isoformat()
        return JSONResponse(content=gateway_dict)

    except Exception as e:
        logger.error(f"API-ошибка обновления шлюза {gateway_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gateway_for_enterprise(gateway_id: int):
    try:
        await delete_goip_gateway(gateway_id)
        # Также удаляем папку с конфигом
        gateway_path = CONFIG_BASE_PATH / str(gateway_id)
        if gateway_path.exists():
            shutil.rmtree(gateway_path)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"API-ошибка удаления шлюза {gateway_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- API для файлов конфигурации ---

async def _save_gateway_config_file(gateway_id: int, file: UploadFile) -> str:
    """Сохраняет загруженный файл в нужную папку и возвращает исходное имя файла."""
    gateway_config_path = CONFIG_BASE_PATH / str(gateway_id)
    gateway_config_path.mkdir(parents=True, exist_ok=True)
    file_path = gateway_config_path / "config.dat"
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    finally:
        file.file.close()
        
    return file_path.name

@router.post("/{enterprise_number}/gateways/{gateway_id}/upload_config", response_class=JSONResponse)
async def upload_gateway_config_api(enterprise_number: str, gateway_id: int, config_file: UploadFile = File(...)):
    """
    Загружает файл конфигурации для шлюза и обновляет запись в БД.
    """
    if not await get_goip_gateway_by_id(gateway_id):
        raise HTTPException(status_code=404, detail="Шлюз не найден")
    
    try:
        original_filename = await _save_gateway_config_file(gateway_id, config_file)
        uploaded_at = datetime.utcnow()
        
        await update_goip_gateway(
            gateway_id=gateway_id,
            config_backup_original_name=original_filename,
            config_backup_uploaded_at=uploaded_at
        )
        return {
            "message": f"Конфиг '{original_filename}' успешно загружен.",
            "original_filename": original_filename,
            "uploaded_at": uploaded_at.isoformat()
        }
    except Exception as e:
        logger.error(f"API-ошибка загрузки конфига для шлюза {gateway_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки файла: {e}")

@router.get("/{enterprise_number}/gateways/{gateway_id}/download_config", response_class=FileResponse)
async def download_gateway_config(enterprise_number: str, gateway_id: int):
    """
    Отдает файл конфигурации для скачивания.
    """
    config_path = CONFIG_BASE_PATH / str(gateway_id)
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Файл конфигурации не найден")
    
    file_path = config_path / "config.dat"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл конфигурации отсутствует на диске")
    
    return FileResponse(
        path=file_path,
        filename=f"{enterprise_number}_config.dat",
        media_type='application/octet-stream'
    )

# --- API для сообщений в бот ---

@router.post("/{number}/send_message", response_class=JSONResponse)
async def send_message_api(number: str, request: Request):
    data = await request.json()
    message = data.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым.")
    
    ent = await get_enterprise_by_number(number)
    if not ent or not ent.get("bot_token"):
        raise HTTPException(status_code=404, detail="Предприятие или его токен не найдены.")

    bot_token = ent["bot_token"]
    owner_chat_id = ent.get("chat_id")
    
    # Получаем всех подписчиков для этого бота из telegram_users
    pool = await db_services.get_pool()
    if not pool:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    async with pool.acquire() as conn:
        user_rows = await conn.fetch(
            "SELECT tg_id FROM telegram_users WHERE bot_token = $1",
            bot_token
        )
    
    # Собираем список получателей: владелец + подписчики
    recipients = set()
    
    # Добавляем владельца бота (если есть chat_id)
    if owner_chat_id:
        recipients.add(str(owner_chat_id))
    
    # Добавляем подписчиков
    for row in user_rows:
        recipients.add(str(row["tg_id"]))
    
    if not recipients:
        raise HTTPException(status_code=404, detail="Нет получателей для отправки (нет владельца и подписчиков)")
    
    # Отправляем сообщение всем получателям
    success_count = 0
    error_count = 0
    errors = []
    
    for chat_id in recipients:
        success, error = await send_message_to_bot(bot_token, chat_id, message)
        if success:
            success_count += 1
        else:
            error_count += 1
            errors.append(f"Chat {chat_id}: {error}")
    
    if success_count > 0:
        result = {"detail": f"Сообщение отправлено {success_count} получателям"}
        if error_count > 0:
            result["warnings"] = f"{error_count} ошибок: {'; '.join(errors)}"
        return JSONResponse(result)
    else:
        raise HTTPException(status_code=500, detail=f"Не удалось отправить всем получателям: {'; '.join(errors)}")
