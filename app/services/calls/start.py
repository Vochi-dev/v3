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
    bridge_store,
)

async def process_start(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие Asterisk 'start' (начало звонка):
      1. Извлекает UniqueId, телефон (raw_phone), определяет, внутренний ли звонок.
      2. Формирует текст уведомления: для внутреннего звонка — стрелка между номерами;
         для внешнего — форматирует номер и при наличии истории добавляет информацию о прошлом звонке.
      3. Экранирует символы '<' и '>' в тексте для безопасности HTML.
      4. Пытается найти подходящее hangup-сообщение для reply_to (через get_relevant_hangup_message_id).
      5. Отправляет сообщение в Telegram (с reply_to, если найдено).
      6. Сохраняет message_id в общем хранилище bridge_store, обновляет сопоставления:
         • call_pair_message_map (чтобы новые события можно было reply-to),
         • hangup_message_map (история hangup).
      7. Асинхронно сохраняет запись о сообщении в БД (await save_telegram_message).
    Возвращает {"status": "sent"} при успешной отправке или {"status": "error", "error": ...} в случае ошибки.
    """

    # ───────── Извлечение и предобработка данных ─────────
    uid       = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    # Форматируем номер в международный, или оставляем как есть, если внутренний
    phone     = format_phone_number(raw_phone)
    exts      = data.get("Extensions", [])
    # CallType == 2 означает внутренний звонок
    is_int    = data.get("CallType", 0) == 2
    # Callee — первый элемент списка Extensions, если он не пуст
    callee    = exts[0] if exts else ""

    # ───────── Формирование текста уведомления ─────────
    if is_int:
        # Внутренний звонок: просто показываем стрелку между внутренними номерами
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        # Внешний входящий: форматируем номер, показываем LastCallInfo (если есть)
        display = phone if not phone.startswith("+000") else "Номер не определен"
        text    = f"🛎️ Входящий звонок\n💰 {display}"
        # Если была история hangup для этого номера, добавляем её
        last    = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    # Экранируем символы '<' и '>' для безопасного отображения в HTML
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    # ───────── Поиск reply_to и отправка сообщения ─────────
    try:
        # Ищем ID последнего hangup-сообщения для reply_to
        reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_int)
        if reply_id:
            sent = await bot.send_message(
                chat_id,
                safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_start] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # ───────── Обновление глобального состояния ─────────
    # Сохраняем message_id в bridge_store, чтобы при следующих событиях знать, что удалить
    bridge_store[uid] = sent.message_id
    # Обновляем сопоставление пары caller–callee и hangup-историю
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    # ───────── Сохранение в базу данных ─────────
    # Обязательно используем await, чтобы корутина отработала полностью
    await save_telegram_message(
        sent.message_id,
        "start",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )

    return {"status": "sent"}
