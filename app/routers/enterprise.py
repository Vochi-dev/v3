# app/routers/enterprise.py
# -*- coding: utf-8 -*-

import logging
import os
import shutil # Для операций с файлами
from pathlib import Path # Для работы с путями
from typing import List, Dict, Optional # Для аннотаций типов
from datetime import datetime # Для временных меток

from fastapi import APIRouter, Request, Form, status, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import sys
import asyncpg # Для обработки ошибок UniqueViolationError

from app.services.database import (
    get_all_enterprises,
    delete_enterprise as db_delete_enterprise,
)
from app.services.enterprise import send_message_to_bot
from app.services.bot_status import check_bot_status
from app.services.fail2ban import get_banned_count
from app.services.postgres import (
    add_enterprise,
    update_enterprise as postgres_update_enterprise,
    get_enterprise_by_number,
    get_gateways_by_enterprise_number,
    # Функции для шлюзов теперь частично используются
    add_goip_gateway,
    update_goip_gateway,
    delete_goip_gateway,
    get_goip_gateway_by_id
)
from pydantic import BaseModel, constr, conint

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(console_handler)

GOIP_CONFIG_BASE_PATH = Path("/root/asterisk-webhook/goip_configs")

# Функция _save_gateway_config_file пока не будет вызываться из основного CRUD, 
# но оставим ее на случай, если она нужна для download_gateway_config или других частей, которые не трогаем.
async def _save_gateway_config_file(enterprise_number: str, gateway_id_or_temp_idx: int, file: UploadFile, base_path: Path) -> tuple[Optional[str], Optional[str]]:
    if not file or not file.filename:
        return None, None
    original_filename = file.filename
    saved_filename = "config.dat"
    gateway_config_path = base_path / enterprise_number / str(gateway_id_or_temp_idx)
    gateway_config_path.mkdir(parents=True, exist_ok=True)
    file_path = gateway_config_path / saved_filename
    try:
        content = await file.read()
        with open(file_path, "wb") as buffer:
            buffer.write(content)
        logger.info(f"Файл конфигурации '{original_filename}' сохранен как '{file_path}' для шлюза/индекса {gateway_id_or_temp_idx} предприятия {enterprise_number}")
        return saved_filename, original_filename
    except Exception as e:
        logger.error(f"Ошибка сохранения файла конфигурации '{original_filename}' для шлюза/индекса {gateway_id_or_temp_idx}: {e}")
        return None, None
    finally:
        if file: # Убедимся что file существует перед close
          await file.close()

router = APIRouter(prefix="/admin/enterprises", tags=["enterprises"])
templates = Jinja2Templates(directory="app/templates")

# Pydantic-модель для валидации данных нового шлюза
class GatewayCreate(BaseModel):
    gateway_name: constr(strip_whitespace=True, min_length=1)
    line_count: conint(ge=1, le=32)
    custom_boolean_flag: bool = False

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
        {
            "request": request,
            "enterprises": enterprises,
            "banned_count": banned_count
        }
    )

@router.get("/add", response_class=HTMLResponse)
async def add_form(request: Request):
    return templates.TemplateResponse(
        "enterprise_form.html",
        {
            "request": request,
            "action": "add",
            "enterprise": {},
            "gateways": [] # Для формы все еще нужны шлюзы, но обрабатывать их не будем
        }
    )

@router.post("/add", response_class=RedirectResponse)
async def add_enterprise_post(request: Request):
    form_data = await request.form()
    logger.debug(f"ADD RAW FORM DATA (gateways temporarily disabled): {form_data}")

    number = form_data.get("number", "")
    name = form_data.get("name", "")
    bot_token = form_data.get("bot_token", "")
    chat_id = form_data.get("chat_id", "374573193")
    ip = form_data.get("ip", "")
    secret = form_data.get("secret", "")
    host = form_data.get("host", "")
    name2 = form_data.get("name2", "")
    is_enabled = form_data.get("is_enabled") == "true"
    active = form_data.get("active") == "true"

    try:
        scheme_count_str = form_data.get("scheme_count")
        scheme_count = int(scheme_count_str) if scheme_count_str and scheme_count_str.isdigit() else 3
    except (ValueError, TypeError): scheme_count = 3 

    try:
        gsm_line_count_str = form_data.get("gsm_line_count")
        gsm_line_count = int(gsm_line_count_str) if gsm_line_count_str and gsm_line_count_str.isdigit() else 8
    except (ValueError, TypeError): gsm_line_count = 8

    parameter_option_1 = form_data.get("parameter_option_1") == "true"
    parameter_option_2 = form_data.get("parameter_option_2") == "true"
    parameter_option_3 = form_data.get("parameter_option_3") == "true"
    parameter_option_4 = form_data.get("parameter_option_4") == "true"
    parameter_option_5 = form_data.get("parameter_option_5") == "true"
    custom_domain = form_data.get("custom_domain")
    custom_port_str = form_data.get("custom_port")
    try:
        custom_port = int(custom_port_str) if custom_port_str and custom_port_str.isdigit() else None
    except (ValueError, TypeError): custom_port = None

    if not all([number, name, ip, secret, host]):
        raise HTTPException(status_code=400, detail="Отсутствуют обязательные поля предприятия.")

    exists = await get_enterprise_by_number(number)
    if exists:
        raise HTTPException(status_code=400, detail=f"Предприятие с номером '{number}' уже существует.")

    try:
        await add_enterprise(
            number=number, name=name, bot_token=bot_token, chat_id=chat_id, ip=ip,
            secret=secret, host=host, name2=name2, is_enabled=is_enabled, active=active,
            scheme_count=scheme_count, gsm_line_count=gsm_line_count,
            parameter_option_1=parameter_option_1, parameter_option_2=parameter_option_2,
            parameter_option_3=parameter_option_3, parameter_option_4=parameter_option_4,
            parameter_option_5=parameter_option_5, custom_domain=custom_domain, custom_port=custom_port
        )
        logger.info(f"ADD: Предприятие {number} - '{name}' успешно добавлено (обработка шлюзов временно отключена).")

        # ======================================================================
        # ВОССТАНАВЛИВАЕМ ЛОГИКУ ОБРАБОТКИ ШЛЮЗОВ ДЛЯ НОВЫХ ПРЕДПРИЯТИЙ
        # ======================================================================
        form_gateways_data = []
        idx = 0
        while True:
            gateway_key_prefix = f"gateways[{idx}]"
            # Проверяем наличие хотя бы одного поля для этого индекса шлюза
            if not any(key.startswith(gateway_key_prefix) for key in form_data.keys()):
                break 

            gateway_name = form_data.get(f"{gateway_key_prefix}[gateway_name]", "").strip()
            # Если имя шлюза не указано, считаем, что это конец списка шлюзов или пустой слот
            if not gateway_name:
                idx += 1
                continue
            
            line_count_str = form_data.get(f"{gateway_key_prefix}[line_count]")
            custom_boolean_flag = form_data.get(f"{gateway_key_prefix}[custom_boolean_flag]") == "true"
            config_file: Optional[UploadFile] = form_data.get(f"{gateway_key_prefix}[config_file]")

            line_count: Optional[int] = None
            try:
                line_count = int(line_count_str) if line_count_str and line_count_str.isdigit() else None
            except (ValueError, TypeError):
                pass # line_count останется None

            saved_config_filename: Optional[str] = None
            original_config_filename: Optional[str] = None
            config_uploaded_at: Optional[datetime] = None

            if config_file and config_file.filename:
                logger.debug(f"ADD: Обнаружен файл конфигурации '{config_file.filename}' для нового шлюза '{gateway_name}' индекса {idx}")
                # Для новых шлюзов, ID еще нет. Используем индекс или временный идентификатор для сохранения файла.
                # Функция _save_gateway_config_file принимает gateway_id_or_temp_idx.
                saved_config_filename, original_config_filename = await _save_gateway_config_file(
                    enterprise_number=number,
                    gateway_id_or_temp_idx=idx, # Используем индекс как временный идентификатор папки
                    file=config_file,
                    base_path=GOIP_CONFIG_BASE_PATH
                )
                if saved_config_filename and original_config_filename:
                    config_uploaded_at = datetime.utcnow()
                    logger.info(f"ADD: Файл '{original_config_filename}' для шлюза '{gateway_name}' (индекс {idx}) сохранен как {saved_config_filename}")
                else:
                    logger.warning(f"ADD: Не удалось сохранить файл '{config_file.filename}' для шлюза '{gateway_name}' (индекс {idx})")
            
            logger.debug(f"ADD: Добавление шлюза в БД: Имя='{gateway_name}', Линии={line_count}, Флаг={custom_boolean_flag}, ФайлОриг='{original_config_filename}'")
            await add_goip_gateway(
                enterprise_number=number,
                gateway_name=gateway_name,
                line_count=line_count,
                custom_boolean_flag=custom_boolean_flag,
                config_backup_filename=saved_config_filename,
                config_backup_original_name=original_config_filename,
                config_backup_uploaded_at=config_uploaded_at
            )
            idx += 1
        
        logger.info(f"ADD: Обработка шлюзов для нового предприятия {number} завершена.")
        # ======================================================================

        return RedirectResponse(url="/admin/enterprises", status_code=status.HTTP_303_SEE_OTHER)

    except asyncpg.exceptions.UniqueViolationError as e:
        logger.error(f"ADD ERROR (gateways disabled): Ошибка уникальности при добавлении предприятия {number}: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка: Номер предприятия уже используется. {e}")
    except Exception as e:
        logger.error(f"ADD ERROR (gateways disabled): {str(e)} при добавлении предприятия {number}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

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
            for key, value in gw_dict.items():
                if isinstance(value, datetime):
                    gw_dict[key] = value.isoformat()
            gateways_for_template.append(gw_dict)

    # Получаем query-параметры для уведомлений
    gateway_added = request.query_params.get('gateway_added')

    return templates.TemplateResponse(
        "enterprise_form.html",
        {
            "request": request,
            "action": "edit",
            "enterprise": ent,
            "gateways": gateways_for_template,
            "notification": {
                "gateway_added": gateway_added
            }
        }
    )

@router.post("/{enterprise_number}/gateways", response_class=JSONResponse, status_code=status.HTTP_201_CREATED)
async def create_gateway_for_enterprise(enterprise_number: str, gateway_data: GatewayCreate):
    """
    API-эндпоинт для создания одного шлюза для предприятия.
    """
    try:
        enterprise = await get_enterprise_by_number(enterprise_number)
        if not enterprise:
            raise HTTPException(status_code=404, detail="Предприятие не найдено")

        logger.info(f"API: Создание нового шлюза для предприятия {enterprise_number}: Имя='{gateway_data.gateway_name}'")
        new_gateway = await add_goip_gateway(
            enterprise_number=enterprise_number,
            gateway_name=gateway_data.gateway_name,
            line_count=gateway_data.line_count,
            custom_boolean_flag=gateway_data.custom_boolean_flag,
            config_backup_filename=None,
            config_backup_original_name=None,
            config_backup_uploaded_at=None
        )
        
        response_data = dict(new_gateway)
        for key, value in response_data.items():
            if isinstance(value, datetime):
                response_data[key] = value.isoformat()

        return JSONResponse(content=response_data, status_code=status.HTTP_201_CREATED)

    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"Шлюз с именем '{gateway_data.gateway_name}' уже существует у этого предприятия.")
    except Exception as e:
        logger.error(f"API-ошибка создания шлюза для предприятия {enterprise_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@router.post("/{number}/edit", response_class=RedirectResponse)
async def edit_enterprise_post(request: Request, number: str):
    """
    Обрабатывает обновление ТОЛЬКО основных данных предприятия.
    Логика шлюзов теперь вынесена в отдельный API.
    """
    form_data = await request.form()
    logger.debug(f"EDIT RAW FORM DATA (gateways handled by API): {form_data}")

    # (здесь идет существующая логика обновления данных самого предприятия, я ее не трогаю)
    name = form_data.get("name", "")
    bot_token = form_data.get("bot_token", "")
    chat_id = form_data.get("chat_id", "374573193")
    ip = form_data.get("ip", "")
    secret = form_data.get("secret", "")
    host = form_data.get("host", "")
    name2 = form_data.get("name2", "")
    is_enabled = form_data.get("is_enabled") == "true"
    active = form_data.get("active") == "true"
    try:
        scheme_count = int(form_data.get("scheme_count", 3))
        gsm_line_count = int(form_data.get("gsm_line_count", 8))
    except (ValueError, TypeError):
        scheme_count = 3
        gsm_line_count = 8
    parameter_option_1 = form_data.get("parameter_option_1") == "true"
    parameter_option_2 = form_data.get("parameter_option_2") == "true"
    parameter_option_3 = form_data.get("parameter_option_3") == "true"
    parameter_option_4 = form_data.get("parameter_option_4") == "true"
    parameter_option_5 = form_data.get("parameter_option_5") == "true"
    custom_domain = form_data.get("custom_domain", "")
    try:
        custom_port = int(form_data.get("custom_port")) if form_data.get("custom_port") else None
    except (ValueError, TypeError):
        custom_port = None

    await postgres_update_enterprise(
        number, name, bot_token, chat_id, ip, secret, host, name2, is_enabled, active,
        scheme_count, gsm_line_count, parameter_option_1, parameter_option_2,
        parameter_option_3, parameter_option_4, parameter_option_5,
        custom_domain, custom_port
    )
    logger.info(f"EDIT: Основные данные предприятия {number} - '{name}' успешно обновлены.")
    
    # Вся логика обработки шлюзов из этой функции УДАЛЕНА.
    # Она теперь обрабатывается через новый API-эндпоинт.

    redirect_url = f"/admin/enterprises/{number}/edit?enterprise_saved=true"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)

@router.get("/{enterprise_number}/gateways/{gateway_id}/download_config", response_class=FileResponse)
async def download_gateway_config(enterprise_number: str, gateway_id: int):
    # Эта функция остается, так как она не часть основного CRUD предприятий
    logger.info(f"DOWNLOAD_CONFIG: Запрос на скачивание для шлюза {gateway_id} предприятия {enterprise_number}")
    gateway_data = await get_goip_gateway_by_id(gateway_id) # get_goip_gateway_by_id должен быть импортирован
    if not gateway_data:
        raise HTTPException(status_code=404, detail="Шлюз не найден")
    if gateway_data.get('enterprise_number') != enterprise_number: # Сравнение enterprise_number
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    config_filename = gateway_data.get('config_backup_filename')
    original_filename = gateway_data.get('config_backup_original_name', 'config.dat') # Имя по умолчанию
    if not config_filename:
        raise HTTPException(status_code=404, detail="Файл конфигурации не найден в БД")

    file_path = GOIP_CONFIG_BASE_PATH / enterprise_number / str(gateway_id) / config_filename
    if not file_path.exists() or not file_path.is_file():
        logger.error(f"DOWNLOAD_CONFIG: Файл не найден на диске: {file_path}")
        raise HTTPException(status_code=404, detail="Файл конфигурации не найден на сервере")
    return FileResponse(path=str(file_path), filename=original_filename, media_type='application/octet-stream')

@router.delete("/{number}", response_class=JSONResponse)
async def delete_enterprise_json(request: Request, number: str):
    logger.info(f"DELETE JSON: Запрос на удаление предприятия {number} (обработка папок шлюзов временно отключена)")
    
    # ======================================================================
    # ВРЕМЕННО ОТКЛЮЧЕНА ЛОГИКА УДАЛЕНИЯ ПАПОК ШЛЮЗОВ ДЛЯ ДИАГНОСТИКИ
    # ======================================================================
    # enterprise_gateways = await get_gateways_by_enterprise_number(number)
    # for gw in enterprise_gateways:
    #     gateway_id = gw.get('id')
    #     if gateway_id:
    #         gw_path = GOIP_CONFIG_BASE_PATH / number / str(gateway_id)
    #         if gw_path.exists() and gw_path.is_dir():
    #             try:
    #                 shutil.rmtree(gw_path)
    #                 logger.info(f"DELETE JSON: Удалена директория конфигурации {gw_path}")
    #             except Exception as e_rm:
    #                 logger.error(f"DELETE JSON ERROR: Не удалось удалить {gw_path}: {e_rm}")
    logger.info("DELETE JSON: Логика удаления папок шлюзов временно отключена.")
    # ======================================================================
    
    await db_delete_enterprise(number)
    logger.info(f"DELETE JSON: Предприятие {number} удалено из БД.")
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
