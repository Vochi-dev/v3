from fastapi import FastAPI, Request
import logging
import asyncio

from telegram import Bot

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.services.events import (
    init_database_tables,
    load_hangup_message_history,
    save_asterisk_event,
    save_telegram_message,
)
from app.services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup,
    create_resend_loop,
)

app = FastAPI()
bot = Bot(token=TELEGRAM_BOT_TOKEN)

logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

@app.on_event("startup")
async def on_startup():
    # Подготовка БД
    init_database_tables()
    load_hangup_message_history()
    # Фоновая переотправка «мостов»
    from app.services.calls import create_resend_loop
    asyncio.create_task(create_resend_loop(bot, TELEGRAM_CHAT_ID, dial_cache, bridge_store, active_bridges))


@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")

    # Сохраняем в БД «сырое» событие
    save_asterisk_event(et, uid, token, data)

    # Выбор обработчика по типу события
    handlers = {
        "start": process_start,
        "dial": process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    handler = handlers.get(et)
    if handler:
        # Каждый процесс_* принимает: bot, chat_id, raw data
        return await handler(bot, TELEGRAM_CHAT_ID, data)
    # По умолчанию — игнор
    return {"status": "ignored"}
