from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field
from typing import Optional

from app.services.postgres import (
    add_sip_operator,
    get_all_sip_operators,
    update_sip_operator,
    delete_sip_operator,
)

router = APIRouter()

class SipOperatorSchema(BaseModel):
    id: Optional[int] = None
    name: str = Field(..., min_length=1, max_length=100)
    shablon: str = ""

@router.post("/admin/sip/operators", response_model=SipOperatorSchema, tags=["sip"])
async def create_sip_operator(operator: SipOperatorSchema):
    new_operator = await add_sip_operator(operator.name, operator.shablon)
    if not new_operator:
        raise HTTPException(status_code=500, detail="Could not create SIP operator")
    return new_operator

@router.get("/admin/sip/operators", response_model=list[SipOperatorSchema], tags=["sip"])
async def read_sip_operators():
    return await get_all_sip_operators()

@router.put("/admin/sip/operators/{operator_id}", response_model=SipOperatorSchema, tags=["sip"])
async def edit_sip_operator(operator_id: int, operator: SipOperatorSchema):
    updated_operator = await update_sip_operator(operator_id, operator.name, operator.shablon)
    if not updated_operator:
        raise HTTPException(status_code=404, detail="SIP operator not found")
    return updated_operator

@router.delete("/admin/sip/operators/{operator_id}", status_code=204, tags=["sip"])
async def remove_sip_operator(operator_id: int):
    await delete_sip_operator(operator_id)
    return {} 