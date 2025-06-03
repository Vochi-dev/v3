import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    bridge_store,
    active_bridges,
)

async def process_hangup(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'hangup':
    — удаляет все по UID,
    — рассчитывает длительность,
    — формирует итоговый текст,
    — отправляет (возможно reply_to),
    — обновляет историю и сохраняет в БД.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "") or ""
    exts      = data.get("Extensions", []) or []
    connected = data.get("ConnectedLineNum", "") or ""
    is_int    = bool(exts and exts[0].isdigit() and len(exts[0]) <= 4)
    callee    = exts[0] if exts else connected

    # Чистим память
    bridge_store.pop(uid, None)
    dial_cache.pop(uid, None)
    active_bridges.pop(uid, None)

    # Рассчитываем duration
    dur = ""
    try:
        from datetime import datetime
        secs = int((
            datetime.fromisoformat(data.get("EndTime", "")) -
            datetime.fromisoformat(data.get("StartTime", ""))
        ).total_seconds())
        dur = f"{secs//60:02}:{secs%60:02}"
    except:
        pass

    # Формируем display-номер
    phone   = format_phone_number(caller)
    display = phone if not phone.startswith("+000") else "Номер не определен"
    cs = int(data.get("CallStatus", -1))
    ct = int(data.get("CallType", -1))

    # Итоговый текст
    if is_int:
        m = ("✅ Успешный внутренний звонок\n" if cs == 2 else "❌ Абонент не ответил\n")
        m += f"{caller} ➡️ {callee}\n⌛ {dur}"
    else:
        if ct == 1 and cs == 0:
            m = f"⬆️ ❌ Абонент не ответил\n💰 {display}"
        elif cs == 2:
            m = f"✅ Завершённый звонок\n💰 {display}\n⌛ {dur}"
        else:
            m = f"❌ Завершённый звонок\n💰 {display}\n⌛ {dur}"

    safe_text = m.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_hangup] => chat={chat_id}, text={safe_text!r}")

    # Выбираем reply_to
    reply_id = get_relevant_hangup_message_id(caller, callee, is_int)
    if not reply_id:
        # fallback на pair-map, если нужно
        pass

    try:
        if reply_id:
            sent = await bot.send_message(
                chat_id, safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_hangup] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Обновляем историю
    update_call_pair_message(caller, callee, sent.message_id, is_int)
    update_hangup_message_map(caller, callee, sent.message_id, is_int, cs, ct, exts)

    # Сохраняем в БД
    await save_telegram_message(
        sent.message_id,
        "hangup",
        data.get("Token", ""),
        caller,
        callee,
        is_int
    )
    return {"status": "sent"}
