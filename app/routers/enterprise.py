# app/routers/enterprise.py
from fastapi import APIRouter, Depends
from .admin import require_login           # общая проверка куки

router = APIRouter(prefix="/admin/enterprises")

# ───────── список предприятий ─────────
@router.get("/",  dependencies=[Depends(require_login)])
@router.get("",   dependencies=[Depends(require_login)])   # вариант без «/»
async def list_enterprises():
    # TODO: получить реальные данные из БД
    return {"msg": "список предприятий"}
