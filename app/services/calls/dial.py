import logging
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,     # (в dial мы не используем, но можно оставить)
    get_last_call_info,                 # (также не нужен здесь, но пусть будет)
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    bridge_store,
)

async def process_dial(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает Asterisk-событие 'dial':
      1. Извлекает UniqueId, телефон (raw_phone), определяет call_type.
      2. Удаляет предыдущее 'start'-сообщение (bridge_store).
      3. Формирует текст (внутренний или внешний, с LastCallInfo для внешнего).
      4. Отправляет в Telegram (parse_mode="HTML").
      5. Сохраняет информацию в dial_cache.
      6. Обновляет call_pair_message_map и hangup_message_map.
      7. Сохраняет запись в БД (await save_telegram_message).
    """

    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

    uid = data.get("UniqueId", "")
    # Берём номер из разных ключей на случай, если Asterisk шлёт не всегда "Phone"
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    phone     = format_phone_number(raw_phone)
    exts      = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int    = call_type == 2
    callee    = exts[0] if exts else ""

    # ───────── Шаг 2. Удаляем прошлый "start"-месседж ─────────
    if uid in bridge_store:
        try:
            await bot.delete_message(chat_id, bridge_store.pop(uid))
        except Exception:
            pass

    # ───────── Шаг 3. Формируем текст ─────────
    if is_int:
        # Внутренний звонок
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        # Внешний звонок: проверяем, не +000... (неопределён)
        display = phone if (phone and not phone.startswith("+000")) else "Номер не определен"

        if call_type == 1:
            # Исходящий набор
            text = (
                f"⬆️ <b>Набираем номер</b>\n"
                f"☎️ {', '.join(exts)} ➡️\n"
                f"💰 {display}"
            )
        else:
            # Входящий разговор
            lines = "\n".join(f"☎️ {e}" for e in exts)
            text  = (
                f"🛎️ <b>Входящий разговор</b>\n"
                f"💰 {display} ➡️\n"
                f"{lines}"
            )
        # Если есть история последнего hangup, добавляем её
        last = get_last_call_info(raw_phone if call_type != 1 else callee)
        if last:
            text += f"\n\n{last}"

    # Экранируем html-спецсимволы
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_dial] => chat={chat_id}, text={safe_text!r}")

    # ───────── Шаг 4. Отправляем сообщение в Telegram ─────────
    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_dial] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # ───────── Шаг 5. Сохраняем в dial_cache ─────────
    dial_cache[uid] = {
        "caller":     raw_phone,
        "extensions": exts,
        "call_type":  call_type,
        "token":      data.get("Token", "")
    }

    # ───────── Шаг 6. Обновляем историю для reply-to ─────────
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    # ───────── Шаг 7. Сохраняем в БД (await!) ─────────
    await save_telegram_message(
        sent.message_id,
        "dial",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )

    return {"status": "sent"}
