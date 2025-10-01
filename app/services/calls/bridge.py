import logging
from telegram import Bot
from telegram.error import BadRequest
import json
import hashlib
import asyncio
from datetime import datetime, timedelta

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from app.services.postgres import get_pool
from app.services.metadata_client import metadata_client, extract_internal_phone_from_channel, extract_line_id_from_exten
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

# ═══════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ═══════════════════════════════════════════════════════════════════

# Словарь для отслеживания активных мостов
active_bridges = {}

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

# ═══════════════════════════════════════════════════════════════════
# ЛОГИКА ВЫБОРА ПРАВИЛЬНОГО BRIDGE ДЛЯ ОТПРАВКИ
# ═══════════════════════════════════════════════════════════════════

def should_send_bridge(data: dict) -> bool:
    """
    Определяет нужно ли отправлять данный bridge в Telegram.
    
    Логика:
    - Отправляем bridge если у него есть CallerIDNum и ConnectedLineNum
    - Пропускаем "пустые" или неполные bridge события
    """
    caller = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    bridge_id = data.get("BridgeUniqueid", "")
    
    logging.info(f"[should_send_bridge] Checking bridge {bridge_id}: caller='{caller}', connected='{connected}'")
    
    # Основное условие: должны быть и caller и connected
    if not caller or not connected:
        logging.info(f"[should_send_bridge] Skipping bridge - missing caller or connected")
        return False
    
    # Пропускаем bridge с пустыми или некорректными номерами
    if caller in ["", "unknown", "<unknown>"] or connected in ["", "unknown", "<unknown>"]:
        logging.info(f"[should_send_bridge] Skipping bridge - invalid numbers")
        return False
    
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
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

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
    for msg_id in messages_to_delete:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
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
    
    # ВАЖНО: В bridge роли могут быть перевернуты!
    # Для исходящих: CallerIDNum=внешний, ConnectedLineNum=внутренний
    # Для входящих: CallerIDNum=внешний, ConnectedLineNum=внутренний (так же!)
    # Различаем по тому, кто инициатор
    
    if caller_internal and connected_internal:
        call_direction = "internal"
        internal_ext = caller or connected
        external_phone = None
    elif not caller_internal and connected_internal:
        # Внешний номер в caller, внутренний в connected
        # Это может быть как входящий, так и исходящий
        # Предполагаем что это ИСХОДЯЩИЙ (т.к. ConnectedLineNum - это кто звонит)
        call_direction = "outgoing" 
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
    
    # Для исходящих и входящих звонков получаем обогащённые данные
    if call_direction in ["incoming", "outgoing"] and enterprise_number != "0000":
        try:
            enriched_data = await metadata_client.enrich_message_data(
                enterprise_number=enterprise_number,
                internal_phone=internal_ext if internal_ext else None,
                line_id=trunk if trunk else None,
                external_phone=external_phone if external_phone else None
            )
            logging.info(f"[send_bridge_to_single_chat] Enriched data: {enriched_data}")
        except Exception as e:
            logging.error(f"[send_bridge_to_single_chat] Error enriching metadata: {e}")

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
        else:
            # Для входящих оставляем старую логику
            text = f"☎️{manager_display} 📞➡️ 💰{display_external}📞"
            if trunk_display:
                text += f"\n{trunk_display}"
    
    else:
        # Неопределенный тип
        text = f"☎️{caller} 📞➡️ ☎️{connected}📞"

    # ───────── Шаг 5. Отправляем сообщение ─────────
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
                reply_to_message_id=reply_to_msg_id
            )
            logging.info(f"[send_bridge_to_single_chat] Sent bridge as comment to message {reply_to_msg_id}")
        else:
            # Отправляем как обычное сообщение
            message = await bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
        
        message_id = message.message_id
        logging.info(f"[send_bridge_to_single_chat] Sent bridge message {message_id}")
        
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
