from dotenv import load_dotenv
load_dotenv()


from fastapi import FastAPI, Request
import logging
import asyncio

from dotenv import load_dotenv
load_dotenv()  # Загружаем переменные из .env

from telegram import Bot

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
print(f"🔑 TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN}")

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

# in-memory stores
dial_cache = {}
bridge_store = {}
active_bridges = {}

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
    # Фоновый цикл переотправки «мостов»
    asyncio.create_task(
        create_resend_loop(
            dial_cache,
            bridge_store,
            active_bridges,
            bot,
            TELEGRAM_CHAT_ID
        )
    )

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et = event_type.lower()
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")

    # Сохраняем «сырое» событие в БД
    save_asterisk_event(et, uid, token, data)

    # Диспатчим в нужный обработчик
    handlers = {
        "start": process_start,
        "dial": process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    handler = handlers.get(et)
    if handler:
        # передаём только три аргумента — bot, chat_id и сам запрос
        return await handler(bot, TELEGRAM_CHAT_ID, data)

    return {"status": "ignored"}
