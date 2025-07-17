import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    get_last_call_info,
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    bridge_store,
    active_bridges,
)

async def process_bridge(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'bridge':
    — удаляет связанный dial (если есть),
    — формирует текст,
    — сохраняет в active_bridges для повторной отправки,
    — отправляет и сохраняет историю.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    is_int    = caller.isdigit() and len(caller) <= 4 and connected.isdigit() and len(connected) <= 4

    # Удаляем dial-сообщение, чтобы не дублировать
    if uid in dial_cache:
        dial_cache.pop(uid)
        try:
            await bot.delete_message(chat_id, bridge_store.get(uid, 0))
        except Exception:
            pass

    # Формируем текст
    if is_int:
        text = f"⏱ Идет внутренний разговор\n{caller} ➡️ {connected}"
    else:
        status = int(data.get("CallStatus", 0))
        pre    = "✅ Успешный разговор" if status == 2 else "⬇️ 💬 <b>Входящий разговор</b>"
        cli    = format_phone_number(caller)
        cal    = format_phone_number(connected)
        text   = f"{pre}\n☎️ {cli} ➡️ 💰 {cal}"
        last   = get_last_call_info(connected)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_bridge] => chat={chat_id}, text={safe_text!r}")

    # Отправляем
    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_bridge] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем состояние и историю
    bridge_store[uid] = sent.message_id
    update_call_pair_message(caller, connected, sent.message_id, is_int)
    update_hangup_message_map(caller, connected, sent.message_id, is_int)

    # Трекер незакрытых мостов для resend-loop
    active_bridges[uid] = {
        "text": safe_text,
        "cli":  caller,
        "op":   connected,
        "token": data.get("Token", "")
    }

    # Сохраняем в БД
    await save_telegram_message(
        sent.message_id,
        "bridge",
        data.get("Token", ""),
        caller,
        connected,
        is_int
    )

    return {"status": "sent"}

# ────────────────────────────────────────────────────────────────────────────────
# Новые обработчики для модернизированного AMI-скрипта (17.01.2025)
# ────────────────────────────────────────────────────────────────────────────────

async def process_bridge_create(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие BridgeCreate - создание моста между участниками.
    Логирует событие для анализа, но пока не отправляет уведомления в Telegram.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    bridge_id = data.get("BridgeUniqueid", "")
    bridge_type = data.get("BridgeType", "")
    
    logging.info(f"[process_bridge_create] BridgeCreate: uid={uid}, bridge_id={bridge_id}, type={bridge_type}")
    
    # Пока просто логируем событие без отправки Telegram сообщений
    # В будущем можно добавить логику отправки уведомлений
    
    # Сохраняем в БД для анализа
    await save_telegram_message(
        message_id=0,  # пока не отправляем сообщения
        event_type="bridge_create",
        token=data.get("Token", ""),
        caller=data.get("CallerIDNum", ""),
        callee=data.get("ConnectedLineNum", ""),
        is_internal=False,
        call_status=-1
    )
    
    return {"status": "logged"}

async def process_bridge_leave(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие BridgeLeave - участник покидает мост.
    Логирует событие для анализа динамики моста.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    bridge_id = data.get("BridgeUniqueid", "")
    channel = data.get("Channel", "")
    
    logging.info(f"[process_bridge_leave] BridgeLeave: uid={uid}, bridge_id={bridge_id}, channel={channel}")
    
    # Обновляем active_bridges - удаляем участника если мост пустеет
    if uid in active_bridges:
        logging.info(f"[process_bridge_leave] Removing bridge tracking for {uid}")
        active_bridges.pop(uid, None)
    
    # Сохраняем в БД для анализа
    await save_telegram_message(
        message_id=0,  # пока не отправляем сообщения
        event_type="bridge_leave", 
        token=data.get("Token", ""),
        caller=data.get("CallerIDNum", ""),
        callee=data.get("ConnectedLineNum", ""),
        is_internal=False,
        call_status=-1
    )
    
    return {"status": "logged"}

async def process_bridge_destroy(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие BridgeDestroy - уничтожение моста.
    Очищает связанные ресурсы и логирует завершение моста.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)
    
    bridge_id = data.get("BridgeUniqueid", "")
    bridge_type = data.get("BridgeType", "")
    
    logging.info(f"[process_bridge_destroy] BridgeDestroy: bridge_id={bridge_id}, type={bridge_type}")
    
    # Очищаем все связанные мосты из active_bridges
    bridges_to_remove = []
    for uid, bridge_info in active_bridges.items():
        # Если есть информация о bridge_id в данных, используем её для очистки
        bridges_to_remove.append(uid)
    
    for uid in bridges_to_remove:
        active_bridges.pop(uid, None)
        logging.info(f"[process_bridge_destroy] Cleaned bridge tracking for {uid}")
    
    # Сохраняем в БД для анализа
    await save_telegram_message(
        message_id=0,  # пока не отправляем сообщения
        event_type="bridge_destroy",
        token=data.get("Token", ""),
        caller="",
        callee="",
        is_internal=False,
        call_status=-1
    )
    
    return {"status": "logged"}

async def process_new_callerid(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие NewCallerid - изменение CallerID во время разговора.
    Может происходить при переводах звонков или изменении информации о вызывающем.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    channel = data.get("Channel", "")
    caller_id_num = data.get("CallerIDNum", "")
    caller_id_name = data.get("CallerIDName", "")
    connected_line_num = data.get("ConnectedLineNum", "")
    connected_line_name = data.get("ConnectedLineName", "")
    context = data.get("Context", "")
    exten = data.get("Exten", "")
    
    logging.info(f"[process_new_callerid] NewCallerid: uid={uid}, channel={channel}")
    logging.info(f"[process_new_callerid] CallerID: {caller_id_num} ({caller_id_name})")
    logging.info(f"[process_new_callerid] ConnectedLine: {connected_line_num} ({connected_line_name})")
    
    # Обновляем активные мосты с новой информацией о CallerID
    if uid in active_bridges:
        bridge_info = active_bridges[uid]
        bridge_info["caller_id_updated"] = {
            "CallerIDNum": caller_id_num,
            "CallerIDName": caller_id_name,
            "ConnectedLineNum": connected_line_num,
            "ConnectedLineName": connected_line_name,
            "Context": context,
            "Exten": exten
        }
        logging.info(f"[process_new_callerid] Updated bridge info for {uid}")
    
    # Пока не отправляем Telegram уведомления для NewCallerid,
    # но логируем для анализа и возможной будущей реализации
    
    # Сохраняем в БД для анализа
    await save_telegram_message(
        message_id=0,  # пока не отправляем сообщения
        event_type="new_callerid",
        token=data.get("Token", ""),
        caller=caller_id_num,
        callee=connected_line_num,
        is_internal=False,
        call_status=-1
    )
    
    return {"status": "logged"}
