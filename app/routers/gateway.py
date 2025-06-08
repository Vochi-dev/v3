# app/routers/gateway.py
import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Depends, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.services.postgres import (
    get_gsm_lines_by_gateway_id,
    get_goip_gateway_by_id,
    get_gsm_line_by_id,
    update_gsm_line,
    GsmLine
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["gateway"])
templates = Jinja2Templates(directory="app/templates")

class GsmLineUpdate(BaseModel):
    line_name: Optional[str] = None
    phone_number: Optional[str] = None
    prefix: Optional[str] = None

@router.get("/gateways/{gateway_id}/modal", response_class=HTMLResponse)
async def get_gateway_modal_content(request: Request, gateway_id: int):
    """
    Возвращает HTML-содержимое для модального окна со списком GSM-линий.
    """
    gateway = await get_goip_gateway_by_id(gateway_id)
    if not gateway:
        return HTMLResponse("<p>Шлюз не найден.</p>", status_code=404)
    
    lines = await get_gsm_lines_by_gateway_id(gateway_id)
    
    return templates.TemplateResponse(
        "gateway_modal.html",
        {
            "request": request,
            "gateway": gateway,
            "lines": lines
        }
    )

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

@router.get("/gateways/{gateway_id}/gsm-lines", response_model=List[GsmLine])
async def get_gsm_lines_for_gateway(gateway_id: int):
    """
    Возвращает список GSM-линий для указанного шлюза. (JSON API)
    """
    lines = await get_gsm_lines_by_gateway_id(gateway_id)
    if not lines:
        # Возвращаем пустой список, если линии не найдены, это не ошибка 404
        return []
    return lines

@router.get("/gsm-lines/{line_id}", response_class=JSONResponse)
async def get_gsm_line_details(line_id: int):
    """
    Возвращает детали конкретной GSM-линии в формате JSON для заполнения формы редактирования.
    """
    line = await get_gsm_line_by_id(line_id)
    if not line:
        raise HTTPException(status_code=404, detail="Линия не найдена")
    return JSONResponse(content=dict(line))


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
        
        # Конвертируем datetime в строку для JSON-сериализации
        if updated_line.get("created_at") and isinstance(updated_line["created_at"], datetime):
            updated_line["created_at"] = updated_line["created_at"].isoformat()
        if updated_line.get("updated_at") and isinstance(updated_line["updated_at"], datetime):
            updated_line["updated_at"] = updated_line["updated_at"].isoformat()
            
        return JSONResponse(content=updated_line)
    except Exception as e:
        logger.error(f"Ошибка обновления GSM линии {line_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка на сервере: {e}") 