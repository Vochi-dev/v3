# app/routers/user_requests.py
from fastapi import APIRouter, Depends
from .admin import require_login

router = APIRouter(prefix="/admin/requests")

# ───────── список заявок ─────────
@router.get("/",  dependencies=[Depends(require_login)])
@router.get("",   dependencies=[Depends(require_login)])
async def list_requests():
    # TODO: вернуть заявки из БД
    return {"msg": "список заявок"}

# ───────── одобрение заявки ─────────
@router.post("/{request_id}/approve", dependencies=[Depends(require_login)])
async def approve_request(request_id: int):
    # TODO: обновить статус в БД
    return {"msg": f"заявка {request_id} одобрена"}
