# app/routers/mobile.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.services.postgres import (
    get_all_mobile_operators,
    add_mobile_operator,
    update_mobile_operator,
    delete_mobile_operator,
)

router = APIRouter(prefix="/admin/mobile", tags=["mobile"])

class MobileOperatorCreate(BaseModel):
    name: str
    shablon: Optional[str] = ""

class MobileOperator(MobileOperatorCreate):
    id: int

@router.get("/operators", response_model=List[MobileOperator])
async def list_operators():
    return await get_all_mobile_operators()

@router.post("/operators", response_model=MobileOperator, status_code=201)
async def create_operator(operator: MobileOperatorCreate):
    return await add_mobile_operator(operator.name, operator.shablon)

@router.put("/operators/{operator_id}", response_model=MobileOperator)
async def edit_operator(operator_id: int, operator: MobileOperatorCreate):
    updated = await update_mobile_operator(operator_id, operator.name, operator.shablon)
    if not updated:
        raise HTTPException(status_code=404, detail="Mobile operator not found")
    return updated

@router.delete("/operators/{operator_id}", status_code=204)
async def remove_operator(operator_id: int):
    await delete_mobile_operator(operator_id)
    return 