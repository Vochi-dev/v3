from fastapi import FastAPI, Request
import logging
import asyncio

from telegram import Bot

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from services.events import (
    init_database_tables,
    load_hangup_message_history,
    save_asterisk_event,
    save_telegram_message,
)
from services.calls import (
    process_start,
    process_dial,
    process_bridge,
    process_hangup,
    create_resend_loop,
)

app = FastAPI()
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# temporary in-memory stores for calls
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
    # prepare DB
    init_database_tables()
    load_hangup_message_history()
    # background task: re-send active "bridges" every 10s
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

    # save raw Asterisk event
    save_asterisk_event(et, uid, token, data)

    # dispatch to handler
    handlers = {
        "start": process_start,
        "dial": process_dial,
        "bridge": process_bridge,
        "hangup": process_hangup,
    }
    handler = handlers.get(et)
    if handler:
        # each process_* signature: (bot, chat_id, raw_data, dial_cache, bridge_store, active_bridges)
        return await handler(bot, TELEGRAM_CHAT_ID, data, dial_cache, bridge_store, active_bridges)

    return {"status": "ignored"}
