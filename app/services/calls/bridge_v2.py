import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from .utils import format_phone_number, delete_previous_messages, save_v2_message, is_internal_number, escape_html

async def process_bridge_v2(bot: Bot, chat_id: int, data: dict):
    """
    Новая версия обработчика bridge - отправляет сообщение в Telegram,
    удаляет предыдущие сообщения для этого unique_id.
    Согласно правилам из Пояснение.txt: "Каждое следующее событие уничтожает предыдущее"
    """
    
    # Сохраняем лог в asterisk_logs для истории
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")
    caller = escape_html(data.get("CallerIDNum", ""))
    connected = escape_html(data.get("ConnectedLineNum", ""))
    trunk = data.get("Trunk", "")
    bridge_id = data.get("BridgeUniqueid", "")
    
    logging.info(f"[process_bridge_v2] BRIDGE event: uid={uid}, bridge_id={bridge_id}, caller={caller}, connected={connected}")
    
    # Правило: "Событие бридж на один звонок отображается только одно"
    # Проверяем по BridgeUniqueid - один мост = одно сообщение
    from .utils import v2_messages, bridge_sent_cache, bridge_recent_pairs
    
    if bridge_id and bridge_id in bridge_sent_cache:
        logging.info(f"[process_bridge_v2] BRIDGE already sent for bridge_id={bridge_id}, skipping")
        return {"status": "skipped", "reason": "bridge_already_sent"}
    
    # Дополнительная проверка: если такие же участники уже были в bridge недавно (последние 10 сек)
    import time
    current_time = time.time()
    bridge_key = f"{caller}_{connected}"
    
    recent = bridge_recent_pairs.get(bridge_key)
    if recent and (current_time - recent) < 10:  # 10 секунд
        logging.info(f"[process_bridge_v2] Recent BRIDGE for {bridge_key}, skipping")
        return {"status": "skipped", "reason": "bridge_too_recent"}
    
    # Удаляем предыдущие сообщения для этого unique_id
    deleted = await delete_previous_messages(bot, chat_id, uid)
    logging.info(f"[process_bridge_v2] Deleted {len(deleted)} previous messages for {uid}")
    
    # Определяем тип звонка
    caller_internal = is_internal_number(caller)
    connected_internal = is_internal_number(connected)
    
    # Формируем сообщение BRIDGE согласно шаблону из Пояснение.txt
    if caller_internal and connected_internal:
        # Внутренний: ☎️185 Баевский Евгений 📞➡️ ☎️186 Петров Петр📞
        message_text = f"☎️{caller} 📞➡️ ☎️{connected}📞"
    elif caller_internal:
        # Исходящий: ☎️185 Баевский Евгений 📞➡️ 💰+375 (29) 625-40-70📞
        formatted_connected = format_phone_number(connected)
        message_text = f"☎️{caller} 📞➡️ 💰{formatted_connected}📞\nЛиния: {trunk}"
    else:
        # Входящий: ☎️185 Баевский Евгений 📞➡️ 💰+375 (29) 625-40-70📞
        formatted_caller = format_phone_number(caller)
        message_text = f"☎️{connected} 📞➡️ 💰{formatted_caller}📞\nЛиния: {trunk}"
    
    try:
        # Отправляем сообщение в Telegram
        tg_message = await bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode="HTML"
        )
        
        # Тип звонка уже определен выше для формирования сообщения
        if caller_internal and connected_internal:
            call_type = 2  # внутренний
            is_internal = True
        elif caller_internal:
            call_type = 1  # исходящий  
            is_internal = False
        else:
            call_type = 0  # входящий
            is_internal = False
        
        # Сохраняем в обычном формате
        await save_telegram_message(
            message_id=tg_message.message_id,
            event_type="bridge_v2",
            token=token,
            caller=caller,
            callee=connected,
            is_internal=is_internal,
            call_status=2,  # успешное соединение
            call_type=call_type
        )
        
        # Дополнительно сохраняем в памяти для V2 логики удаления
        save_v2_message(uid, chat_id, tg_message.message_id, "bridge_v2")
        
        # Запоминаем что для этого bridge_id уже отправили сообщение
        if bridge_id:
            bridge_sent_cache.add(bridge_id)
        
        # Запоминаем участников и время для дополнительной фильтрации  
        bridge_recent_pairs[bridge_key] = current_time
        
        logging.info(f"[process_bridge_v2] Sent BRIDGE message {tg_message.message_id} to chat {chat_id}")
        return {"status": "sent", "message_id": tg_message.message_id, "deleted_previous": len(deleted)}
        
    except Exception as e:
        logging.error(f"[process_bridge_v2] Failed to send BRIDGE message: {e}")
        raise e 