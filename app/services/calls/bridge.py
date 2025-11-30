import logging
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
import json
import hashlib
import asyncio
from datetime import datetime, timedelta

from app.services.events import save_telegram_message
from app.services.postgres import get_pool
from app.services.metadata_client import metadata_client, extract_internal_phone_from_channel, extract_line_id_from_exten
from app.utils.call_tracer import log_telegram_event
from .utils import (
    format_phone_number,
    bridge_store,
    bridge_store_by_chat,
    
    # Новые функции для группировки событий
    get_phone_for_grouping,
    should_send_as_comment,
    should_replace_previous_message,
    update_phone_tracker,
    is_internal_number,
    phone_message_tracker,
)
from app.utils.user_phones import (
    get_all_internal_phones_by_tg_id,
    get_bot_owner_chat_id,
    get_enterprise_secret,
)

# ═══════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ═══════════════════════════════════════════════════════════════════

# Словарь для отслеживания активных мостов
active_bridges = {}

# Словарь для отслеживания уже отправленных bridge по BridgeUniqueid
# Ключ: BridgeUniqueid, Значение: timestamp отправки
sent_bridges = {}

# ═══════════════════════════════════════════════════════════════════
# ОСНОВНАЯ ФУНКЦИЯ ОБРАБОТКИ BRIDGE СОБЫТИЙ
# ═══════════════════════════════════════════════════════════════════

async def process_bridge(bot: Bot, chat_id: int, data: dict):
    """
    ФИНАЛЬНЫЙ обработчик события 'bridge' (17.01.2025):
    - Проверяет является ли bridge ПРАВИЛЬНЫМ для отправки
    - Отправляет МГНОВЕННО только правильные bridge
    - НЕ кэширует, НЕ ждет 5 секунд
    """
    logging.info(f"[process_bridge] RAW DATA = {data!r}")
    
    # Получаем данные для логирования
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")
    
    # Получаем номер предприятия из БД по Token (name2)
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
                    logging.info(f"[process_bridge] Resolved Token '{token}' -> enterprise '{enterprise_number}'")
                else:
                    logging.warning(f"[process_bridge] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_bridge] Failed to resolve enterprise_number: {e}")
    
    # Проверяем нужно ли отправлять этот bridge
    if should_send_bridge(data):
        # Отправляем bridge МГНОВЕННО в конкретный чат (используем переданные bot и chat_id)
        result = await send_bridge_to_single_chat(bot, chat_id, data)
        return result
    else:
        logging.info(f"[process_bridge] Skipping bridge - not the right one to send")
        return {"status": "skipped"}

# ────────────────────────────────────────────────────────────────────────────────
# Новые обработчики для модернизированного AMI-скрипта (17.01.2025)
# ────────────────────────────────────────────────────────────────────────────────

async def process_bridge_create(bot: Bot, chat_id: int, data: dict):
    """
    Обрабатывает событие BridgeCreate - создание моста между участниками.
    Логирует событие для анализа, но пока не отправляет уведомления в Telegram.
    """
    uid = data.get("UniqueId", "")
    bridge_id = data.get("BridgeUniqueid", "")
    bridge_type = data.get("BridgeType", "")
    
    logging.info(f"[process_bridge_create] BridgeCreate: uid={uid}, bridge_id={bridge_id}, type={bridge_type}")
    
    # ───────── Логирование bridge_create события в Call Logger ─────────
    enterprise_number = "unknown"
    try:
        token = data.get("Token", "")
        if token:
            pool = await get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT number FROM enterprises WHERE name2 = $1",
                    token
                )
                if result:
                    enterprise_number = result['number']
                else:
                    logging.warning(f"[process_bridge_create] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_bridge_create] Failed to resolve enterprise_number: {e}")

    try:
        logging.info(f"[process_bridge_create] bridge_create event: uid={uid}, bridge_id={bridge_id}")
    except Exception as e:
        logging.warning(f"[process_bridge_create] Failed to process bridge_create event: {e}")
    
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
    uid = data.get("UniqueId", "")
    bridge_id = data.get("BridgeUniqueid", "")
    channel = data.get("Channel", "")
    
    logging.info(f"[process_bridge_leave] BridgeLeave: uid={uid}, bridge_id={bridge_id}, channel={channel}")
    
    # ───────── Логирование bridge_leave события в Call Logger ─────────
    enterprise_number = "unknown"
    try:
        token = data.get("Token", "")
        if token:
            pool = await get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT number FROM enterprises WHERE name2 = $1",
                    token
                )
                if result:
                    enterprise_number = result['number']
                else:
                    logging.warning(f"[process_bridge_leave] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_bridge_leave] Failed to resolve enterprise_number: {e}")

    logging.info(f"[process_bridge_leave] bridge_leave event: uid={uid}, bridge_id={bridge_id}")
    
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
    bridge_id = data.get("BridgeUniqueid", "")
    bridge_type = data.get("BridgeType", "")
    
    logging.info(f"[process_bridge_destroy] BridgeDestroy: bridge_id={bridge_id}, type={bridge_type}")
    
    # ───────── Логирование bridge_destroy события в Call Logger ─────────
    enterprise_number = "unknown"
    try:
        token = data.get("Token", "")
        if token:
            pool = await get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT number FROM enterprises WHERE name2 = $1",
                    token
                )
                if result:
                    enterprise_number = result['number']
                else:
                    logging.warning(f"[process_bridge_destroy] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_bridge_destroy] Failed to resolve enterprise_number: {e}")

    try:
        # ИСПРАВЛЕНО: bridge_destroy не имеет UniqueId, поэтому НЕ логируем его в call_traces
        # Это событие уровня моста, а не звонка - оно не привязано к конкретному UniqueId
        # Если нужно отслеживать разрушение мостов - это должна быть отдельная таблица
        logging.info(f"[process_bridge_destroy] Skipping bridge_destroy logging - no UniqueId (bridge_id={bridge_id})")
    except Exception as e:
        logging.warning(f"[process_bridge_destroy] Failed to process bridge_destroy event: {e}")
    
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
    
    # ───────── Логирование new_callerid события в Call Logger ─────────
    enterprise_number = "unknown"
    try:
        token = data.get("Token", "")
        if token:
            pool = await get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT number FROM enterprises WHERE name2 = $1",
                    token
                )
                if result:
                    enterprise_number = result['number']
                else:
                    logging.warning(f"[process_new_callerid] Enterprise not found for Token '{token}'")
    except Exception as e:
        logging.error(f"[process_new_callerid] Failed to resolve enterprise_number: {e}")

    logging.info(f"[process_new_callerid] new_callerid event: uid={uid}")
    
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

# ═══════════════════════════════════════════════════════════════════
# ЛОГИКА ВЫБОРА ПРАВИЛЬНОГО BRIDGE ДЛЯ ОТПРАВКИ
# ═══════════════════════════════════════════════════════════════════

def should_send_bridge(data: dict) -> bool:
    """
    Определяет нужно ли отправлять данный bridge в Telegram.
    
    Логика:
    - Отправляем bridge если у него есть CallerIDNum и ConnectedLineNum
    - Пропускаем "пустые" или неполные bridge события
    - Пропускаем промежуточные bridge с ExternalInitiated=true (internal→external)
    - Пропускаем дубликаты по BridgeUniqueid (если уже отправляли bridge с таким же BridgeUniqueid)
    """
    from .utils import is_internal_number
    import time
    
    caller = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    bridge_id = data.get("BridgeUniqueid", "")
    
    logging.info(f"[should_send_bridge] Checking bridge {bridge_id}: caller='{caller}', connected='{connected}'")
    
    # ПРИМЕЧАНИЕ: Проверка дубликатов по BridgeUniqueid перенесена на уровень _dispatch_to_all
    # чтобы не блокировать отправку для всех chat_ids после первого
    # Проверяем только если это вызов из send_bridge_to_telegram (не из _dispatch_to_all)
    if bridge_id and bridge_id in sent_bridges and not data.get("_from_dispatch_to_all"):
        time_since_sent = time.time() - sent_bridges[bridge_id]
        logging.info(f"[should_send_bridge] Skipping bridge {bridge_id} - already sent {time_since_sent:.1f}s ago (duplicate)")
        return False
    
    # Основное условие: должны быть и caller и connected
    if not caller or not connected:
        logging.info(f"[should_send_bridge] Skipping bridge - missing caller or connected")
        return False
    
    # Пропускаем bridge с пустыми или некорректными номерами
    if caller in ["", "unknown", "<unknown>"] or connected in ["", "unknown", "<unknown>"]:
        logging.info(f"[should_send_bridge] Skipping bridge - invalid numbers")
        return False
    
    # НОВОЕ ПРАВИЛО: Пропускаем ПРОМЕЖУТОЧНЫЕ bridge события с ExternalInitiated=true
    # Промежуточный bridge: внутренний номер → внешний номер (после bridge_create из CRM)
    # Настоящий bridge: внешний номер → внутренний номер (реальный разговор)
    external_initiated = data.get("ExternalInitiated", False)
    if external_initiated:
        # Определяем направление: если CallerIDNum внутренний, а ConnectedLineNum внешний - это промежуточный bridge
        caller_is_internal = is_internal_number(caller)
        connected_is_external = not is_internal_number(connected)
        
        if caller_is_internal and connected_is_external:
            logging.info(f"[should_send_bridge] Skipping bridge {bridge_id} - ExternalInitiated=true intermediate bridge (internal→external)")
            return False
        else:
            logging.info(f"[should_send_bridge] Allowing bridge {bridge_id} - ExternalInitiated=true but real conversation bridge (external→internal)")
    
    # ВАЖНО: Сохраняем BridgeUniqueid в sent_bridges только если это НЕ вызов из _dispatch_to_all
    # При вызове из _dispatch_to_all, дубликаты контролируются на уровне _dispatch_to_all
    if bridge_id and not data.get("_from_dispatch_to_all"):
        sent_bridges[bridge_id] = time.time()
        logging.info(f"[should_send_bridge] Marked bridge {bridge_id} as sent (standalone call)")
    
    logging.info(f"[should_send_bridge] Bridge {bridge_id} is valid for sending")
    return True

# ═══════════════════════════════════════════════════════════════════
# ОТПРАВКА BRIDGE СООБЩЕНИЙ В ТЕЛЕГРАМ  
# ═══════════════════════════════════════════════════════════════════

async def send_bridge_to_telegram(data: dict):
    """
    Отправляет bridge сообщение в телеграм.
    ИСПРАВЛЕНО: Добавлена логика получения bot и chat_id из токена.
    """
    try:
        # Получаем bot и chat_ids для токена
        token = data.get("Token", "")
        if not token:
            logging.error(f"[send_bridge_to_telegram] No token in bridge data")
            return {"status": "error", "error": "No token"}
            
        # Логика получения бота и получателей (из main.py)
        from telegram import Bot
        from app.services.postgres import get_pool
        
        pool = await get_pool()
        if not pool:
            logging.error(f"[send_bridge_to_telegram] Database pool not available")
            return {"status": "error", "error": "No database"}
        
        async with pool.acquire() as conn:
            ent_row = await conn.fetchrow(
                "SELECT bot_token FROM enterprises WHERE name2 = $1", 
                token
            )
            if not ent_row:
                logging.error(f"[send_bridge_to_telegram] Unknown enterprise token: {token}")
                return {"status": "error", "error": "Unknown token"}
            
            bot_token = ent_row["bot_token"]
            
            user_rows = await conn.fetch(
                "SELECT tg_id FROM telegram_users WHERE bot_token = $1",
                bot_token
            )
        
        tg_ids = [int(row["tg_id"]) for row in user_rows]
        # Добавляем суперюзера если его нет
        SUPERUSER_TG_ID = 374573193
        if SUPERUSER_TG_ID not in tg_ids:
            tg_ids.append(SUPERUSER_TG_ID)
            
        bot = Bot(token=bot_token)
        
        # Отправляем в каждый чат
        results = []
        for chat_id in tg_ids:
            result = await send_bridge_to_single_chat(bot, chat_id, data)
            results.append(result)
        
        return {"status": "success", "results": results}
        
    except Exception as e:
        logging.error(f"[send_bridge_to_telegram] Error: {e}")
        return {"status": "error", "error": str(e)}


async def send_bridge_to_single_chat(bot: Bot, chat_id: int, data: dict):
    """
    Отправляет bridge событие в телеграм (реальная обработка).
    """
    # Получаем номер для группировки событий
    phone_for_grouping = get_phone_for_grouping(data)
    logging.info(f"[send_bridge_to_single_chat] Phone for grouping: {phone_for_grouping}")

    # ───────── Шаг 2. Удаляем предыдущие bridge сообщения ─────────
    messages_to_delete = []
    
    # Проверяем, есть ли уже bridge для этого номера телефона
    should_replace, msg_to_delete = should_replace_previous_message(phone_for_grouping, 'bridge', chat_id)
    if should_replace and msg_to_delete:
        messages_to_delete.append(msg_to_delete)
        logging.info(f"[send_bridge_to_single_chat] Found previous message {msg_to_delete} to delete for phone {phone_for_grouping}")
    
    # Также проверяем bridge_store по UniqueId (старая логика)
    uid = data.get("UniqueId", "")
    if uid in bridge_store:
        old_bridge_msg = bridge_store.pop(uid)
        if old_bridge_msg not in messages_to_delete:
            messages_to_delete.append(old_bridge_msg)
            logging.info(f"[send_bridge_to_single_chat] Found bridge in store {old_bridge_msg} to delete for uid {uid}")

    # Удаляем старые сообщения
    ent_num = data.get("_enterprise_number", "")
    for msg_id in messages_to_delete:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            log_telegram_event(ent_num, "delete", chat_id, "bridge", msg_id, uid, "")
            logging.info(f"[send_bridge_to_single_chat] Deleted previous bridge message {msg_id}")
        except BadRequest as e:
            logging.warning(f"[send_bridge_to_single_chat] Could not delete message {msg_id}: {e}")
        except Exception as e:
            logging.error(f"[send_bridge_to_single_chat] Error deleting message {msg_id}: {e}")

    logging.info(f"[send_bridge_to_single_chat] After cleanup, proceeding to create new bridge message")

    # ───────── Шаг 3. Определяем тип звонка ─────────
    caller = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    
    # Проверяем что это за звонок
    caller_internal = is_internal_number(caller)
    connected_internal = is_internal_number(connected)
    external_initiated = data.get("ExternalInitiated", False)
    
    # ВАЖНО: В bridge роли могут быть перевернуты!
    # Для исходящих: CallerIDNum=внешний, ConnectedLineNum=внутренний
    # Для входящих: CallerIDNum=внешний, ConnectedLineNum=внутренний (так же!)
    # Различаем по ExternalInitiated (если есть) или по тому, кто инициатор
    
    if caller_internal and connected_internal:
        call_direction = "internal"
        internal_ext = caller or connected
        external_phone = None
    elif not caller_internal and connected_internal:
        # Внешний номер в caller, внутренний в connected
        # Это может быть как входящий, так и исходящий
        # Проверяем ExternalInitiated для точного определения
        if external_initiated:
            call_direction = "incoming"  # Внешний инициировал = входящий
        else:
            call_direction = "outgoing"  # Внутренний инициировал = исходящий
        internal_ext = connected  # внутренний номер менеджера
        external_phone = caller   # внешний номер клиента
    elif caller_internal and not connected_internal:
        call_direction = "outgoing"
        internal_ext = caller
        external_phone = connected
    else:
        call_direction = "unknown"
        internal_ext = caller or connected
        external_phone = connected or caller

    logging.info(f"[send_bridge_to_single_chat] Bridge: {caller} <-> {connected}, call_direction={call_direction}")

    # ───────── Шаг 3.5. Получаем обогащённые метаданные ─────────
    token = data.get("Token", "")
    
    # Получаем enterprise_number из БД по токену
    from app.services.postgres import get_pool
    pool = await get_pool()
    enterprise_number = "0000"
    if pool and token:
        async with pool.acquire() as conn:
            ent_row = await conn.fetchrow(
                "SELECT number FROM enterprises WHERE name2 = $1", token
            )
            if ent_row:
                enterprise_number = ent_row["number"]
    
    # Обогащаем метаданными для bridge
    enriched_data = {}
    
    # Извлекаем trunk из Channel (например: "SIP/0001363-00000001" → "0001363")
    trunk = data.get("Trunk", "")
    if not trunk:
        channel = data.get("Channel", "")
        if channel and "/" in channel and "-" in channel:
            # Формат: SIP/0001363-00000001
            parts = channel.split("/")
            if len(parts) > 1:
                trunk_part = parts[1].split("-")[0]
                trunk = trunk_part
                logging.info(f"[send_bridge_to_single_chat] Extracted trunk '{trunk}' from Channel '{channel}'")
    
    # Используем pre-enriched данные (уже сделано в main.py)
    enriched_data = data.get("_enriched_data", {})
    if enriched_data:
        logging.info(f"[send_bridge_to_single_chat] Using pre-enriched data: {enriched_data}")
    else:
        logging.warning(f"[send_bridge_to_single_chat] No pre-enriched data available")

    # ───────── Шаг 4. Формируем текст согласно Пояснению ─────────
    if call_direction == "internal":
        # Внутренний звонок с обогащением ФИО
        caller_display = caller
        connected_display = connected
        
        # ФИО участников отключено для устранения блокировок
        
        text = f"☎️{caller_display} 📞➡️ ☎️{connected_display}📞"
    
    elif call_direction in ["incoming", "outgoing"]:
        # Внешний звонок с обогащением метаданными
        if external_phone:
            # ИСПРАВЛЕНО: заменяем <unknown> на безопасный текст
            if external_phone == "<unknown>" or external_phone.startswith("<unknown>") or external_phone.endswith("<unknown>"):
                display_external = "Номер не определен"
            else:
                formatted_external = format_phone_number(external_phone)
                display_external = formatted_external if not formatted_external.startswith("+000") else "Номер не определен"
                
                # Обогащаем: сначала номер, потом ФИО в скобках
                customer_name = enriched_data.get("customer_name", "")
                if customer_name:
                    display_external = f"{display_external} ({customer_name})"
        else:
            display_external = "Номер не определен"
        
        # Обогащаем ФИО менеджера
        manager_fio = enriched_data.get("manager_name", "")
        if manager_fio and not manager_fio.startswith("Доб."):
            # Есть реальное ФИО - показываем "ФИО (номер)"
            manager_display = f"{manager_fio} ({internal_ext})"
        else:
            # Нет ФИО или это "Доб.XXX" - показываем просто номер
            manager_display = internal_ext
        
        # Формируем линию: антенна + название (без номера линии)
        line_name = enriched_data.get("line_name", "")
        trunk_display = f"📡 {line_name}" if line_name else f"📡 {trunk}"
        
        if call_direction == "outgoing":
            # Заголовок для исходящего
            text = f"🔗 Идет исходящий разговор\n☎️{manager_display} 📞➡️ 💰{display_external}📞"
            if trunk_display:
                text += f"\n{trunk_display}"
        elif call_direction == "incoming":
            # Заголовок для входящего
            text = f"🔗 Идет входящий разговор\n💰{display_external}📞 ➡️ ☎️{manager_display}"
            if trunk_display:
                text += f"\n{trunk_display}"
        else:
            # Для неопределенных оставляем старую логику
            text = f"☎️{manager_display} 📞➡️ 💰{display_external}📞"
            if trunk_display:
                text += f"\n{trunk_display}"
    
    else:
        # Неопределенный тип
        text = f"☎️{caller} 📞➡️ ☎️{connected}📞"

    # ───────── Шаг 5. Создаём кнопки мониторинга (только для внешних звонков) ─────────
    reply_markup = None
    
    # Кнопки мониторинга только для внешних звонков (не для internal)
    if call_direction in ["incoming", "outgoing"] and internal_ext:
        try:
            # Получаем данные для кнопок
            owner_chat_id = await get_bot_owner_chat_id(token)
            enterprise_secret = await get_enterprise_secret(token)
            
            # Если текущий chat_id НЕ владелец - получаем ВСЕ его внутренние номера
            if owner_chat_id != chat_id and enterprise_secret:
                user_internal_phones = await get_all_internal_phones_by_tg_id(
                    enterprise_number=enterprise_number,
                    telegram_tg_id=chat_id
                )
                
                if user_internal_phones:
                    # target - кого мониторим (internal_ext - тот кто разговаривает)
                    # monitor_from - кто мониторит (номера текущего пользователя)
                    
                    # ФИЛЬТРУЕМ: исключаем номер который сейчас разговаривает
                    available_phones = [phone for phone in user_internal_phones if phone != internal_ext]
                    
                    buttons = []
                    for monitor_from in available_phones:
                        # Создаём 3 кнопки для каждого номера пользователя
                        row = [
                            InlineKeyboardButton(
                                text=f"👂 Прослушивание {monitor_from}",
                                callback_data=f"monitor:09:{internal_ext}:{monitor_from}:{enterprise_secret}"
                            ),
                            InlineKeyboardButton(
                                text=f"💬 Суфлирование {monitor_from}",
                                callback_data=f"monitor:01:{internal_ext}:{monitor_from}:{enterprise_secret}"
                            ),
                            InlineKeyboardButton(
                                text=f"🎙️ Конференция {monitor_from}",
                                callback_data=f"monitor:02:{internal_ext}:{monitor_from}:{enterprise_secret}"
                            )
                        ]
                        buttons.append(row)
                    
                    # Создаём keyboard только если есть доступные номера
                    if buttons:
                        reply_markup = InlineKeyboardMarkup(buttons)
                        logging.info(
                            f"[send_bridge_to_single_chat] Added {len(available_phones)*3} monitor button(s) "
                            f"for available_phones={available_phones}, target={internal_ext} (excluded from {user_internal_phones})"
                        )
                    else:
                        logging.info(
                            f"[send_bridge_to_single_chat] No available phones for monitoring "
                            f"(user only has {internal_ext} which is currently talking)"
                        )
        except Exception as e:
            logging.error(f"[send_bridge_to_single_chat] Error creating monitor buttons: {e}")

    # ───────── Шаг 6. Отправляем сообщение ─────────
    logging.info(f"[send_bridge_to_single_chat] => chat={chat_id}, text='{text}'")
    
    try:
        # Проверяем нужно ли отправлять как комментарий
        should_comment, reply_to_msg_id = should_send_as_comment(phone_for_grouping, 'bridge', chat_id)
        
        # Если предыдущие сообщения были удалены, НЕ отправляем как комментарий
        if messages_to_delete and reply_to_msg_id in messages_to_delete:
            should_comment = False
            reply_to_msg_id = None
            logging.info(f"[send_bridge_to_single_chat] Previous message was deleted, sending as standalone message")
        
        if should_comment and reply_to_msg_id:
            # Отправляем как комментарий к предыдущему сообщению
            message = await bot.send_message(
                chat_id=chat_id, 
                text=text, 
                parse_mode='HTML',
                reply_to_message_id=reply_to_msg_id,
                reply_markup=reply_markup
            )
            logging.info(f"[send_bridge_to_single_chat] Sent bridge as comment to message {reply_to_msg_id}")
        else:
            # Отправляем как обычное сообщение
            message = await bot.send_message(
                chat_id=chat_id, 
                text=text, 
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        
        message_id = message.message_id
        # Логируем в call_tracer
        ent_num = data.get("_enterprise_number", "")
        log_telegram_event(ent_num, "send", chat_id, "bridge", message_id, uid, text)
        logging.info(f"[send_bridge_to_single_chat] Sent bridge message {message_id}")
        
        # ШАГ 1: Получаем и удаляем предыдущее сообщение (dial)
        try:
            import httpx, asyncio
            await asyncio.sleep(0.1)  # race condition fix
            
            # Получаем сообщения из кэша
            url = f"http://localhost:8020/telegram/messages/{phone_for_grouping}/{chat_id}"
            async with httpx.AsyncClient(timeout=2.0) as client:
                logging.info(f"[BRIDGE] 📞 GET {url}")
                resp = await client.get(url)
                logging.info(f"[BRIDGE] 📥 status={resp.status_code}")
                
                if resp.status_code == 200:
                    cache_data = resp.json()
                    messages = cache_data.get("messages", {})
                    logging.info(f"[BRIDGE] 📥 Got cache: {list(messages.keys())}")
                else:
                    logging.warning(f"[BRIDGE] ⚠️ No prev messages (404)")
                    messages = {}
            
            # Удаляем START, DIAL и предыдущий BRIDGE из Telegram
            ent_num = data.get("_enterprise_number", "")
            for event_type in ["start", "dial", "bridge"]:
                if event_type in messages:
                    msg_id = messages[event_type]
                    logging.info(f"[BRIDGE] 🗑️ Deleting {event_type.upper()} msg={msg_id}")
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                        log_telegram_event(ent_num, "delete", chat_id, event_type, msg_id, uid, "")
                        logging.info(f"[BRIDGE] ✅ {event_type.upper()} deleted")
                    except Exception as e:
                        logging.error(f"[BRIDGE] ❌ Delete {event_type.upper()} failed: {e}")
            
            # Удаляем START, DIAL и BRIDGE из кэша
            if messages:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.delete(f"{url}?event_types=start&event_types=dial&event_types=bridge")
                    logging.info(f"[BRIDGE] 🧹 Cleared cache")
        except Exception as e:
            logging.error(f"[BRIDGE] ❌ Error: {e}")
        
        # ШАГ 2: Сохраняем свой message_id в кэш
        try:
            import httpx
            async with httpx.AsyncClient(timeout=1.0) as client:
                await client.post("http://localhost:8020/telegram/message", json={
                    "phone": phone_for_grouping,
                    "chat_id": chat_id,
                    "event_type": "bridge",
                    "message_id": message_id
                })
            logging.info(f"[BRIDGE] ✅ Cached msg={message_id}")
        except Exception as e:
            logging.error(f"[BRIDGE] ❌ Cache failed: {e}")
        
        # Сохраняем в трекер для последующих комментариев
        update_phone_tracker(phone_for_grouping, message_id, 'bridge', data, chat_id)
        
        # Сохраняем в bridge_store
        bridge_store_by_chat[chat_id][uid] = message_id
        
        # Сохраняем в базу
        token = data.get("Token", "")
        caller = data.get("CallerIDNum", "")
        callee = data.get("ConnectedLineNum", "")
        is_internal = call_direction == "internal"
        
        await save_telegram_message(
            message_id=message_id,
            event_type="bridge", 
            token=token,
            caller=caller,
            callee=callee,
            is_internal=is_internal
        )
        
        logging.info(f"[send_bridge_to_single_chat] Successfully sent bridge message {message_id} for {phone_for_grouping}")
        
        return {"status": "success", "message_id": message_id}
        
    except Exception as e:
        logging.error(f"[send_bridge_to_single_chat] Error sending bridge message: {e}")
        return {"status": "error", "error": str(e)}
