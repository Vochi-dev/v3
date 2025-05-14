# app/routers/enterprise.py
from fastapi import APIRouter, Request, Depends, HTTPException, status
from .admin import require_login           # проверка авторизации

router = APIRouter(prefix="/admin/enterprises")

# ───────── примеры маршрутов ─────────
@router.get("", dependencies=[Depends(require_login)])
async def list_enterprises():
    # TODO: вернуть список предприятий из БД
    return {"msg": "список предприятий"}

@router.post("", dependencies=[Depends(require_login)])
async def create_enterprise(payload: dict):
    # TODO: добавить предприятие
    return {"msg": "предприятие создано", "data": payload}
