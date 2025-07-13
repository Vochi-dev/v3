import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from .utils import format_phone_number, save_v2_message, escape_html

async def process_start_v2(bot: Bot, chat_id: int, data: dict):
    """
    Новая версия обработчика start - отправляет сообщение в Telegram,
    но сохраняет message_id для удаления следующим событием.
    Согласно правилам из Пояснение.txt: "Каждое следующее событие уничтожает предыдущее"
    """
    
    # Сохраняем лог в asterisk_logs для истории
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")
    raw_phone = escape_html(data.get("Phone", "") or "")
    trunk = escape_html(data.get("Trunk", ""))
    
    logging.info(f"[process_start_v2] START event: uid={uid}, phone={raw_phone}, token={token}")
    
    # Форматируем номер телефона
    formatted_phone = format_phone_number(raw_phone) if raw_phone else "Неизвестный номер"
    
    # Формируем сообщение START согласно шаблону из Пояснение.txt
    message_lines = [
        f"💰{formatted_phone} ➡️ Приветствие",
        f"Линия: {trunk}",
        "⏳ Входящий звонок..."
    ]
    
    message_text = "\n".join(message_lines)
    
    try:
        # Отправляем сообщение в Telegram
        tg_message = await bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode="HTML"
        )
        
        # Сохраняем в обычном формате
        await save_telegram_message(
            message_id=tg_message.message_id,
            event_type="start_v2",
            token=token,
            caller=raw_phone,
            callee="",  # пока неизвестно
            is_internal=False,  # входящий звонок
            call_status=-1,  # неизвестно
            call_type=0  # входящий
        )
        
        # Дополнительно сохраняем в памяти для V2 логики удаления
        save_v2_message(uid, chat_id, tg_message.message_id, "start_v2")
        
        logging.info(f"[process_start_v2] Sent START message {tg_message.message_id} to chat {chat_id}")
        return {"status": "sent", "message_id": tg_message.message_id}
        
    except Exception as e:
        logging.error(f"[process_start_v2] Failed to send START message: {e}")
        raise e 