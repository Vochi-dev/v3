import aiosqlite
from fastapi import APIRouter, Request
from telegram import Bot
from app.config import DB_PATH
from app.services.events import save_asterisk_event
from app.services.calls import (
    process_start, process_dial, process_bridge, process_hangup
)
from app.services.calls.internal import (
    process_internal_start, process_internal_bridge, process_internal_hangup
)
from app.services.calls.utils import is_internal_number

router = APIRouter()

@router.post("/events/{event_type}")
@router.post("/{event_type}")
async def handle_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")

    await save_asterisk_event(et, uid, token, data)

    # … здесь ваш код по выбору bot_token, списка пользователей, обработчиков …
    # точно так же, как в main.py, но теперь внутри этого роутера

    return {"status": "sent"}
