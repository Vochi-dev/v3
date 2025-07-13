import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from .utils import format_phone_number, delete_previous_messages, save_v2_message, escape_html

async def process_dial_v2(bot: Bot, chat_id: int, data: dict):
    """
    Новая версия обработчика dial - отправляет сообщение в Telegram,
    удаляет предыдущие сообщения для этого unique_id.
    Согласно правилам из Пояснение.txt: "Каждое следующее событие уничтожает предыдущее"
    """
    
    # Сохраняем лог в asterisk_logs для истории
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")
    raw_phone = escape_html(data.get("Phone", "") or data.get("CallerIDNum", "") or "")
    extensions = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    trunk = escape_html(data.get("Trunk", ""))
    
    logging.info(f"[process_dial_v2] DIAL event: uid={uid}, phone={raw_phone}, extensions={extensions}, call_type={call_type}")
    
    # Удаляем предыдущие сообщения для этого unique_id
    deleted = await delete_previous_messages(bot, chat_id, uid)
    logging.info(f"[process_dial_v2] Deleted {len(deleted)} previous messages for {uid}")
    
    # Форматируем номер телефона
    formatted_phone = format_phone_number(raw_phone) if raw_phone else "Неизвестный номер"
    
    # Формируем сообщение DIAL согласно шаблону из Пояснение.txt
    if call_type == 0:  # входящий звонок
        if len(extensions) == 1:
            message_lines = [
                f"💰{formatted_phone} ➡️",
                f"☎️{extensions[0]}",
                f"Линия: {trunk}",
                "📞 Звонок поступил..."
            ]
        else:
            ext_lines = [f"☎️{ext}" for ext in extensions]
            message_lines = [
                f"💰{formatted_phone} ➡️"
            ] + ext_lines + [
                f"Линия: {trunk}",
                "📞 Групповой вызов..."
            ]
    else:  # исходящий звонок
        if extensions:
            message_lines = [
                f"☎️{extensions[0]} ➡️ 💰{formatted_phone}",
                f"Линия: {trunk}",
                "📞 Набираем номер..."
            ]
        else:
            message_lines = [
                f"📞 ➡️ 💰{formatted_phone}",
                f"Линия: {trunk}",
                "📞 Набираем номер..."
            ]
    
    message_text = "\n".join(message_lines)
    
    try:
        # Отправляем сообщение в Telegram
        tg_message = await bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode="HTML"
        )
        
        # Определяем caller/callee в зависимости от типа звонка
        if call_type == 0:  # входящий
            caller = raw_phone
            callee = extensions[0] if extensions else ""
        else:  # исходящий
            caller = extensions[0] if extensions else ""
            callee = raw_phone
        
        # Сохраняем в обычном формате
        await save_telegram_message(
            message_id=tg_message.message_id,
            event_type="dial_v2",
            token=token,
            caller=caller,
            callee=callee,
            is_internal=(call_type == 2),
            call_status=-1,  # неизвестно
            call_type=call_type
        )
        
        # Дополнительно сохраняем в памяти для V2 логики удаления
        save_v2_message(uid, chat_id, tg_message.message_id, "dial_v2")
        
        logging.info(f"[process_dial_v2] Sent DIAL message {tg_message.message_id} to chat {chat_id}")
        return {"status": "sent", "message_id": tg_message.message_id, "deleted_previous": len(deleted)}
        
    except Exception as e:
        logging.error(f"[process_dial_v2] Failed to send DIAL message: {e}")
        raise e 