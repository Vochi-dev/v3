# app/routers/webhooks.py
from fastapi import APIRouter, Request
from telegram import Bot
import asyncio
import logging

from app.services.events import save_asterisk_event
from app.services.calls import process_start, process_dial, process_bridge, process_hangup
from app.services.calls.internal import process_internal_start, process_internal_bridge, process_internal_hangup
from app.services.calls.utils import is_internal_number
from app.services.postgres import get_pool

logger = logging.getLogger(__name__)

async def run_handler_safe(handler, bot, tg_id, data, event_type):
    """
    Безопасно запускает обработчик в фоне с логированием ошибок
    """
    try:
        await handler(bot, tg_id, data)
        logger.info(f"Handler {event_type} completed for tg_id {tg_id}")
    except Exception as e:
        logger.error(f"Handler {event_type} failed for tg_id {tg_id}: {e}")

router = APIRouter()

@router.post("/events/{event_type}")
@router.post("/{event_type}")
async def handle_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")
    await save_asterisk_event(et, uid, token, data)

    # ищем бот-токен предприятия
    pool = await get_pool()
    if not pool:
        return {"status": "database_error"}
    
    async with pool.acquire() as conn:
        ent_row = await conn.fetchrow("SELECT bot_token FROM enterprises WHERE name2 = $1", token)
        if not ent_row:
            return {"status": "no_such_bot"}
        
        bot_token = ent_row["bot_token"]
        bot = Bot(token=bot_token)

        # список подписчиков
        user_rows = await conn.fetch(
            "SELECT tg_id FROM telegram_users WHERE bot_token = $1",
            bot_token,
        )
        if not user_rows:
            return {"status": "no_subscribers"}

    # выбор обработчика
    raw = data.get("Phone", "") or data.get("CallerIDNum", "")
    exts = data.get("Extensions", [])
    ct = int(data.get("CallType", 0))
    is_int = ct == 2 or (is_internal_number(raw) and len(exts)==1 and is_internal_number(exts[0]))
    if et == "start":
        handler = process_internal_start if is_int else process_start
    elif et == "dial":
        handler = process_dial
    elif et == "bridge":
        handler = process_internal_bridge if is_int else process_bridge
    elif et == "hangup":
        handler = process_internal_hangup if is_int else process_hangup
    else:
        return {"status": "ignored"}

    # Для dial, bridge и hangup запускаем в фоне, чтобы не блокировать ответ Asterisk'у
    if et in ["dial", "bridge", "hangup"]:
        # Создаем фоновые задачи для каждого пользователя
        for row in user_rows:
            tg = int(row["tg_id"])
            # Запускаем в фоне без ожидания
            asyncio.create_task(run_handler_safe(handler, bot, tg, data, et))
        
        # Немедленно возвращаем 200 OK Asterisk'у
        return {"status": "processing_async", "users_count": len(user_rows)}
    
    # Для остальных событий (start) выполняем синхронно как раньше
    results = []
    for row in user_rows:
        tg = int(row["tg_id"])
        try:
            res = await handler(bot, tg, data)
            results.append({"tg_id": tg, "status": res.get("status")})
        except Exception as e:
            results.append({"tg_id": tg, "status": "error", "error": str(e)})
    return {"status": "sent", "details": results} 