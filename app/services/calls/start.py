import logging
import asyncio
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.utils.call_tracer import log_telegram_event
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    get_last_call_info,
    update_call_pair_message,
    update_hangup_message_map,
    bridge_store,
    bridge_store_by_chat,
    dial_received_uids,
    # Новые функции для группировки событий
    get_phone_for_grouping,
    should_send_as_comment,
    should_replace_previous_message,
    update_phone_tracker,
)

# Время ожидания перед отправкой start (сек)
# Если за это время пришёл dial - start не отправляем (пустышка)
START_WAIT_FOR_DIAL_SEC = 3

async def process_start(bot: Bot, chat_id: int, data: dict):
    """
    Модернизированный обработчик события 'start' (17.01.2025):
    - Использует новую систему группировки по номеру телефона
    - Применяет форматы сообщений из файла "Пояснение"
    - Поддерживает отправку комментариев к предыдущим сообщениям
    """

    # Получаем номер для группировки событий
    phone_for_grouping = get_phone_for_grouping(data)
    
    # ───────── Шаг 1. Вывод в stdout всего payload ─────────
    logging.info(f"[process_start] RAW DATA = {data!r}")
    logging.info(f"[process_start] Phone for grouping: {phone_for_grouping}")

    # ───────── Шаг 2. Извлечение данных ─────────
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    
    # SKIP: Если Phone - это GSM линия (0001xxx), не отправляем start
    # В этом случае Phone содержит trunk, а не реальный номер звонящего
    # Реальный номер придёт позже в dial событии
    if raw_phone.startswith("0001"):
        logging.info(f"[process_start] SKIP: Phone '{raw_phone}' is a GSM trunk, not a caller number")
        return {"status": "skipped", "reason": "gsm_trunk_phone"}
    
    # ───────── ФИЛЬТР "ПУСТЫХ" START ─────────
    # Ждём 3 сек. Если за это время пришёл dial - значит start "пустышка"
    # (клиент не слушал приветствие), не отправляем его
    call_type = int(data.get("CallType", 0))
    is_incoming = (call_type == 0)  # Входящий звонок
    
    if is_incoming and uid:
        logging.info(f"[process_start] ⏳ Waiting {START_WAIT_FOR_DIAL_SEC}s to check if dial arrives...")
        await asyncio.sleep(START_WAIT_FOR_DIAL_SEC)
        
        if uid in dial_received_uids:
            logging.info(f"[process_start] SKIP: dial already received for {uid}, start is empty (no greeting played)")
            # Очищаем uid из set через 60 сек
            async def cleanup():
                await asyncio.sleep(60)
                dial_received_uids.discard(uid)
            asyncio.create_task(cleanup())
            return {"status": "skipped", "reason": "dial_already_received"}
        
        logging.info(f"[process_start] ✅ No dial yet for {uid}, sending start (greeting is playing)")
    
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int = call_type == 2
    callee = exts[0] if exts else ""
    token = data.get("Token", "")
    
    # Получаем номер предприятия из БД по Token (name2)
    from app.services.postgres import get_pool
    enterprise_number = "0000"  # fallback
    try:
        pool = await get_pool()
        if pool:
            async with pool.acquire() as conn:
                ent_row = await conn.fetchrow(
                    "SELECT number FROM enterprises WHERE name2 = $1 LIMIT 1",
                    token
                )
                if ent_row:
                    enterprise_number = ent_row["number"]
                    logging.info(f"[process_start] Resolved Token '{token}' -> enterprise '{enterprise_number}'")
                else:
                    logging.warning(f"[process_start] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_start] Failed to resolve enterprise_number: {e}")

    # ───────── Шаг 3. Формируем текст согласно Пояснению ─────────
    if is_int:
        # Внутренние звонки обычно не имеют start события
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        # Входящий звонок - используем формат из Пояснения
        display = phone if (phone and not phone.startswith("+000")) else "Номер не определен"
        
        # Обогащаем номер клиента именем если есть
        enriched_data = data.get("_enriched_data", {})
        if enriched_data and enriched_data.get("customer_name"):
            display = f"{display} ({enriched_data['customer_name']})"
        
        # Базовый формат для start события
        text = f"💰{display} ➡️ Приветствие"
        
        # Добавляем информацию о линии с обогащением
        if enriched_data:
            line_name = enriched_data.get("line_name", "")
            if line_name:
                text += f"\n📡{line_name}"
            else:
                # Fallback на сырой номер линии
                trunk_info = data.get("Trunk", "")
                if trunk_info:
                    text += f"\n📡{trunk_info}"
        else:
            # Если нет обогащения, используем сырой номер линии
            trunk_info = data.get("Trunk", "")
            if trunk_info:
                text += f"\n📡{trunk_info}"
        
        # Добавляем историю звонков
        last = get_last_call_info(raw_phone)
        if last:
            # Извлекаем информацию из истории для формата "Звонил: X раз"
            # Пока используем базовую логику, можно будет улучшить
            text += f"\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")

    # ───────── Шаг 3a. Выводим сформированный текст ─────────
    logging.info(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    # ───────── Шаг 4. Отправка в Telegram (БЕЗ REPLY, ПРОСТО ОТПРАВЛЯЕМ) ─────────
    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
        
        # Добавляем message_id к сообщению для отладки
        debug_text = f"{safe_text}\n🔖 msg:{sent.message_id}"
        try:
            await bot.edit_message_text(debug_text, chat_id, sent.message_id, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"[process_start] Failed to add message_id to text: {e}")
        
        # Логируем в call_tracer
        ent_num = data.get("_enterprise_number", enterprise_number)
        log_telegram_event(ent_num, "send", chat_id, "start", sent.message_id, uid, debug_text)
    except BadRequest as e:
        logging.error(f"[process_start] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # ───────── Шаг 6. Обновляем состояние системы ─────────
    bridge_store_by_chat[chat_id][uid] = sent.message_id
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int, chat_id)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int, chat_id=chat_id)
    
    # Обновляем новый трекер для группировки
    update_phone_tracker(phone_for_grouping, sent.message_id, 'start', data, chat_id)

    # ───────── Шаг 7. Сохраняем в БД ─────────
    await save_telegram_message(
        sent.message_id,
        "start",
        token,
        raw_phone,
        callee,
        is_int
    )

    logging.info(f"[process_start] Successfully sent start message {sent.message_id} for {phone_for_grouping}")
    
    # Сохраняем message_id в централизованный кэш
    try:
        import httpx
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.post("http://localhost:8020/telegram/message", json={
                "phone": phone_for_grouping,
                "chat_id": chat_id,
                "event_type": "start",
                "message_id": sent.message_id
            })
        logging.info(f"[START] ✅ Cached msg={sent.message_id} for {phone_for_grouping}:{chat_id}")
    except Exception as cache_e:
        logging.warning(f"[START] ❌ Cache failed: {cache_e}")
    
    return {"status": "sent", "message_id": sent.message_id}
