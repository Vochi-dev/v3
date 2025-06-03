from fastapi import APIRouter, Request, HTTPException
from typing import Dict, Any
from app.services.asterisk_logs import save_asterisk_log

router = APIRouter(prefix="/asterisk", tags=["asterisk"])

@router.post("/webhook")
async def asterisk_webhook(data: Dict[str, Any]):
    """
    Принимает и обрабатывает webhook от Asterisk
    
    Пример данных:
    {
        "Trunk": "0001374",
        "ExtPhone": "",
        "CallType": 1,
        "ExtTrunk": "",
        "Extensions": ["234"],
        "Phone": "79165230932",
        "Token": "375291380627",
        "UniqueId": "1748952962.31"
    }
    """
    try:
        await save_asterisk_log(data)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/calls/{token}")
async def get_calls(token: str, limit: int = 100):
    """Получает историю звонков для предприятия"""
    try:
        from app.services.asterisk_logs import get_call_history
        calls = await get_call_history(token, limit)
        return {"calls": calls}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/call/{unique_id}")
async def get_call(unique_id: str):
    """Получает детали конкретного звонка"""
    try:
        from app.services.asterisk_logs import get_call_details
        events = await get_call_details(unique_id)
        return {"events": events}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 