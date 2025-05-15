import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    get_last_call_info,
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    bridge_store,
)

async def process_dial(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'dial':
    — удаляет предыдущее 'start'-сообщение,
    — формирует текст,
    — отправляет и сохраняет в памяти и БД.
    """
    uid       = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    phone     = format_phone_number(raw_phone)
    exts      = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int    = call_type == 2
    callee    = exts[0] if exts else ""

    # Удаляем прошлый старт
    if uid in bridge_store:
        try:
            await bot.delete_message(chat_id, bridge_store.pop(uid))
        except Exception:
            pass

    # Формируем текст
    if is_int:
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        if call_type == 1:
            text = (
                f"⬆️ <b>Набираем номер</b>\n"
                f"☎️ {', '.join(exts)} ➡️\n"
                f"💰 {display}"
            )
        else:
            lines = "\n".join(f"☎️ {e}" for e in exts)
            text  = (
                f"🛎️ <b>Входящий разговор</b>\n"
                f"💰 {display} ➡️\n"
                f"{lines}"
            )
        last = get_last_call_info(raw_phone if call_type != 1 else callee)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_dial] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_dial] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем в памяти
    dial_cache[uid] = {
        "caller":     raw_phone,
        "extensions": exts,
        "call_type":  call_type,
        "token":      data.get("Token", "")
    }

    # Обновляем историю
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    # Сохраняем в БД
    save_telegram_message(
        sent.message_id,
        "dial",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )

    return {"status": "sent"}
