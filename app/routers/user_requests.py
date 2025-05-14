# app/routers/user_requests.py
from fastapi import APIRouter, Request, Depends, HTTPException, status
from .admin import require_login           # общая проверка логина

router = APIRouter(prefix="/admin/requests")

# ───────── примеры маршрутов ─────────
@router.get("", dependencies=[Depends(require_login)])
async def list_requests():
    # TODO: вернуть список заявок из БД
    return {"msg": "список заявок"}

@router.post("/{request_id}/approve", dependencies=[Depends(require_login)])
async def approve_request(request_id: int):
    # TODO: подтвердить заявку
    return {"msg": f"заявка {request_id} одобрена"}
