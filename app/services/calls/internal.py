# app/services/calls/internal.py

import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from .hangup import create_call_record  # Импортируем функцию создания записи в calls
from app.services.metadata_client import metadata_client, extract_internal_phone_from_channel, extract_line_id_from_exten
from .utils import (
    update_call_pair_message,
    update_hangup_message_map,
    get_relevant_hangup_message_id
)

# локальные сторы
from .utils import hangup_message_map
from .start import bridge_store  # для удаления в start
from .dial import dial_cache      # для удаления в bridge
from . import utils               # общий active_bridges

async def process_internal_start(bot: Bot, chat_id: int, data: dict):
    uid    = data.get("UniqueId", "")
    exts   = data.get("Extensions", [])
    caller = data.get("CallerIDNum", "") or data.get("Phone", "")
    callee = exts[0] if exts else ""
    
    # Получаем номер предприятия для метаданных
    token = data.get("Token", "")
    enterprise_number = token[:4] if token else "0000"
    
    # Обогащаем ФИО участников
    caller_display = caller
    callee_display = callee
    
    try:
        if caller:
            caller_name = await metadata_client.get_manager_name(enterprise_number, caller, short=True)
            if not caller_name.startswith("Доб."):
                caller_display = f"{caller_name} ({caller})"
        
        if callee:
            callee_name = await metadata_client.get_manager_name(enterprise_number, callee, short=True)
            if not callee_name.startswith("Доб."):
                callee_display = f"{callee_name} ({callee})"
    except Exception as e:
        logging.error(f"[process_internal_start] Error enriching names: {e}")

    text = f"🛎️ Внутренний звонок\n{caller_display} ➡️ {callee_display}"
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_internal_start] => {safe_text!r}")

    try:
        # reply-to может быть предыдущий hangup от этих двух
        reply_id = get_relevant_hangup_message_id(caller, callee, True)
        if reply_id:
            sent = await bot.send_message(chat_id, safe_text,
                                          reply_to_message_id=reply_id,
                                          parse_mode="HTML")
        else:
            sent = await bot.send_message(chat_id, safe_text,
                                          parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_internal_start] {e}")
        return {"status": "error", "error": str(e)}

    # для «start»—store
    bridge_store[uid] = sent.message_id
    update_call_pair_message(caller, callee, sent.message_id, True)
    update_hangup_message_map(caller, callee, sent.message_id, True)

    save_telegram_message(
        sent.message_id, "start_internal", data.get("Token",""),
        caller, callee, True
    )
    return {"status": "sent"}


async def process_internal_bridge(bot: Bot, chat_id: int, data: dict):
    uid       = data.get("UniqueId","")
    caller    = data.get("CallerIDNum","")
    connected = data.get("ConnectedLineNum","")

    # удаляем предыдущий dial
    if uid in dial_cache:
        dial_cache.pop(uid, None)
        try:
            await bot.delete_message(chat_id, bridge_store.get(uid,0))
        except:
            pass

    # Получаем номер предприятия для метаданных
    token = data.get("Token", "")
    enterprise_number = token[:4] if token else "0000"
    
    # Обогащаем ФИО участников
    caller_display = caller
    connected_display = connected
    
    try:
        if caller:
            caller_name = await metadata_client.get_manager_name(enterprise_number, caller, short=True)
            if not caller_name.startswith("Доб."):
                caller_display = caller_name
        
        if connected:
            connected_name = await metadata_client.get_manager_name(enterprise_number, connected, short=True)
            if not connected_name.startswith("Доб."):
                connected_display = connected_name
    except Exception as e:
        logging.error(f"[process_internal_bridge] Error enriching names: {e}")

    text = f"⏱ Идет внутренний разговор\n{caller_display} ➡️ {connected_display}"
    safe_text = text.replace("<","&lt;").replace(">","&gt;")
    logging.debug(f"[process_internal_bridge] => {safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_internal_bridge] {e}")
        return {"status":"error","error":str(e)}

    bridge_store[uid] = sent.message_id
    update_call_pair_message(caller, connected, sent.message_id, True)
    update_hangup_message_map(caller, connected, sent.message_id, True)

    # track for resend
    utils.active_bridges[uid] = {
        "text": safe_text, "cli": caller, "op": connected,
        "token": data.get("Token","")
    }

    save_telegram_message(
        sent.message_id, "bridge_internal", data.get("Token",""),
        caller, connected, True
    )
    return {"status":"sent"}


async def process_internal_hangup(bot: Bot, chat_id: int, data: dict):
    uid    = data.get("UniqueId","")
    caller = data.get("CallerIDNum","")
    exts   = data.get("Extensions",[]) or []
    callee = exts[0] if exts else data.get("ConnectedLineNum","")
    token  = data.get("Token", "")

    # Создаем запись в таблице calls для внутренних звонков
    if uid and token:
        # Используем общий UUID токен если он есть (для одинаковых ссылок во всех chat_id)
        shared_uuid = data.get("_shared_uuid_token", None)
        call_record_info = await create_call_record(uid, token, data, shared_uuid)

    # clean up
    bridge_store.pop(uid,None)
    dial_cache.pop(uid,None)
    utils.active_bridges.pop(uid,None)

    # duration calc
    dur = ""
    try:
        from datetime import datetime
        secs = int((datetime.fromisoformat(data["EndTime"])
                    - datetime.fromisoformat(data["StartTime"])).total_seconds())
        dur = f"{secs//60:02}:{secs%60:02}"
    except:
        pass

    # Получаем номер предприятия для метаданных
    enterprise_number = token[:4] if token else "0000"
    
    # Обогащаем ФИО участников
    caller_display = caller
    callee_display = callee
    
    try:
        if caller:
            caller_name = await metadata_client.get_manager_name(enterprise_number, caller, short=True)
            if not caller_name.startswith("Доб."):
                caller_display = caller_name
        
        if callee:
            callee_name = await metadata_client.get_manager_name(enterprise_number, callee, short=True)
            if not callee_name.startswith("Доб."):
                callee_display = callee_name
    except Exception as e:
        logging.error(f"[process_internal_hangup] Error enriching names: {e}")

    # text
    status = int(data.get("CallStatus",0))
    prefix = "✅ Успешный внутренний звонок" if status==2 else "❌ Абонент не ответил"
    text   = f"{prefix}\n{caller_display} ➡️ {callee_display}\n⌛ {dur}"
    safe_text = text.replace("<","&lt;").replace(">","&gt;")
    logging.debug(f"[process_internal_hangup] => {safe_text!r}")

    try:
        reply_id = get_relevant_hangup_message_id(caller, callee, True)
        if reply_id:
            sent = await bot.send_message(
                chat_id, safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_internal_hangup] {e}")
        return {"status":"error","error":str(e)}

    update_call_pair_message(caller, callee, sent.message_id, True)
    update_hangup_message_map(caller, callee, sent.message_id, True)

    save_telegram_message(
        sent.message_id, "hangup_internal", data.get("Token",""),
        caller, callee, True
    )
    return {"status":"sent"}
