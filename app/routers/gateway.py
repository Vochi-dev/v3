# app/routers/gateway.py
import logging
import sys
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.services.postgres import (
    get_gsm_lines_by_gateway_id,
    get_goip_gateway_by_id,
    get_gsm_line_by_id,
    update_gsm_line,
    delete_goip_gateway,
    GsmLine,
    add_goip_gateway,
    create_gsm_lines_for_gateway,
    get_pool
)

logger = logging.getLogger(__name__)
# Устанавливаем ЕДИНЫЙ правильный префикс для всех операций со шлюзами
router = APIRouter(prefix="/admin/gateways", tags=["gateway"])
templates = Jinja2Templates(directory="app/templates")

class GsmLineUpdate(BaseModel):
    line_name: Optional[str] = None
    phone_number: Optional[str] = None
    prefix: Optional[str] = None

class GatewayCreate(BaseModel):
    name: str
    enterprise_id: str

@router.delete("/{gateway_id}", status_code=204)
async def delete_gateway(gateway_id: int):
    """
    Удаляет шлюз и все связанные с ним линии.
    """
    print(f"GATEWAY_ROUTER: Получен DELETE-запрос для шлюза ID: {gateway_id}", file=sys.stderr)
    try:
        await delete_goip_gateway(gateway_id)
        print(f"GATEWAY_ROUTER: Успешное удаление шлюза ID: {gateway_id}", file=sys.stderr)
        return
    except Exception as e:
        print(f"GATEWAY_ROUTER: Ошибка при удалении шлюза ID {gateway_id}: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", status_code=201)
async def add_gateway(data: GatewayCreate):
    """
    Создает шлюз и связанные с ним линии в рамках одной транзакции.
    """
    print(f"GATEWAY_ROUTER: Получен POST-запрос на создание шлюза: {data}", file=sys.stderr)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                new_gateway = await add_goip_gateway(
                    conn=conn,
                    enterprise_number=data.enterprise_id,
                    gateway_name=data.name,
                    line_count=8 # По умолчанию 8 линий
                )
                await create_gsm_lines_for_gateway(
                    conn=conn,
                    gateway_id=new_gateway['id'],
                    gateway_name=new_gateway['gateway_name'],
                    enterprise_number=data.enterprise_id,
                    line_count=8 # По умолчанию 8 линий
                )
                return new_gateway
            except Exception as e:
                logger.error(f"Ошибка при создании шлюза в транзакции: {e}")
                raise HTTPException(status_code=500, detail=str(e))

@router.get("/{gateway_id}/modal", response_class=HTMLResponse)
async def get_gateway_modal_content(request: Request, gateway_id: int):
    """
    Возвращает HTML-содержимое для модального окна со списком GSM-линий
    ДЛЯ КОНКРЕТНОГО ШЛЮЗА.
    """
    print(f"GATEWAY_ROUTER: Запрос на получение модального окна для шлюза ID: {gateway_id}", file=sys.stderr)
    try:
        gateway = await get_goip_gateway_by_id(gateway_id)
        if not gateway:
            print(f"GATEWAY_ROUTER: Шлюз с ID {gateway_id} не найден.", file=sys.stderr)
            raise HTTPException(status_code=404, detail="Шлюз не найден.")
        
        lines = await get_gsm_lines_by_gateway_id(gateway_id)
        print(f"GATEWAY_ROUTER: Для шлюза {gateway_id} найдено {len(lines)} линий.", file=sys.stderr)
        
        return templates.TemplateResponse(
            "gateway_modal.html",
            {
                "request": request,
                "gateway": gateway,
                "lines": lines
            }
        )
    except Exception as e:
        print(f"GATEWAY_ROUTER: КРИТИЧЕСКАЯ ОШИБКА при получении данных для модального окна: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/gsm-lines/{line_id}/edit-modal", response_class=HTMLResponse)
async def get_edit_gsm_line_modal(request: Request, line_id: int):
    """Возвращает HTML-содержимое для модального окна редактирования GSM-линии."""
    line = await get_gsm_line_by_id(line_id)
    if not line:
        raise HTTPException(status_code=404, detail="Линия не найдена")
    
    return templates.TemplateResponse(
        "edit_gsm_line_modal.html",
        {"request": request, "line": line}
    )

@router.put("/gsm-lines/{line_id}", response_class=JSONResponse)
async def update_gsm_line_details(line_id: int, line_data: GsmLineUpdate):
    """
    Обновляет информацию о GSM-линии.
    """
    line = await get_gsm_line_by_id(line_id)
    if not line:
        raise HTTPException(status_code=404, detail="Линия не найдена")
    
    try:
        updated_line = await update_gsm_line(
            line_id=line_id,
            line_name=line_data.line_name,
            phone_number=line_data.phone_number,
            prefix=line_data.prefix
        )
        if not updated_line:
             raise HTTPException(status_code=404, detail="Не удалось обновить линию")
        
        if updated_line.get("created_at") and isinstance(updated_line["created_at"], datetime):
            updated_line["created_at"] = updated_line["created_at"].isoformat()
        if updated_line.get("updated_at") and isinstance(updated_line["updated_at"], datetime):
            updated_line["updated_at"] = updated_line["updated_at"].isoformat()
            
        return JSONResponse(content=updated_line)
    except Exception as e:
        logger.error(f"Ошибка обновления GSM линии {line_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка на сервере: {e}")