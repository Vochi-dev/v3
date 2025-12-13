import logging
import asyncio
import aiohttp
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
import json
import hashlib
import traceback
import uuid
from datetime import datetime

from app.services.events import save_telegram_message
from app.services.calls.bridge import stop_bridge_resend_task
from app.services.customers import upsert_customer_from_hangup
from app.services.postgres import get_pool
from app.services.postgres import get_pool
from app.services.metadata_client import metadata_client, extract_internal_phone_from_channel, extract_line_id_from_exten
from app.utils.call_tracer import log_telegram_event
from app.utils.user_phones import (
    get_all_internal_phones_by_tg_id,
    get_bot_owner_chat_id,
    get_enterprise_secret
)

def get_recording_link_text(call_record_info):
    """
    Формирует кликабельную ссылку на запись разговора для Telegram
    Возвращает пустую строку если нет данных о записи
    """
    if call_record_info and call_record_info.get('call_url'):
        call_url = call_record_info['call_url']
        return f'\n🔉<a href="{call_url}">Запись разговора</a>'
    else:
        # Нет данных о записи - не показываем ничего
        return ''

from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    dial_cache_by_chat,
    bridge_store,
    active_bridges,
    last_hangup_time_by_chat_enterprise,
    bridge_by_internal,
    # Новые функции для группировки событий
    get_phone_for_grouping,
    should_send_as_comment,
    should_replace_previous_message,
    update_phone_tracker,
    is_internal_number,
    phone_message_tracker,
)

async def create_call_record(unique_id: str, token: str, data: dict, uuid_token: str = None):
    """
    Создает запись в таблице calls для hangup события
    """
    pool = await get_pool()
    if not pool:
        logging.error("PostgreSQL pool not available for creating call record")
        return None
    
    try:
        async with pool.acquire() as connection:
            # Получаем enterprise_id по токену
            enterprise_query = """
                SELECT number FROM enterprises 
                WHERE name2 = $1 OR secret = $1
                LIMIT 1
            """
            enterprise_result = await connection.fetchrow(enterprise_query, token)
            enterprise_id = enterprise_result['number'] if enterprise_result else token[:4]
            
            # Создаем хеш токена
            hashed_token = hashlib.md5(token.encode()).hexdigest()
            
            # Извлекаем данные из события
            phone_number = data.get('Phone', data.get('CallerIDNum', ''))
            start_time_str = data.get('StartTime', '')
            end_time_str = data.get('EndTime', '')
            call_status = str(data.get('CallStatus', '0'))
            call_type = str(data.get('CallType', '0'))
            trunk = data.get('Trunk', '')  # Добавлено поле trunk
            
            # 🔍 ПОЛУЧАЕМ TRUNK ИЗ ПРЕДЫДУЩИХ СОБЫТИЙ (dial/start)
            if not trunk:
                try:
                    trunk_query = """
                        SELECT raw_data->'Trunk' as trunk_data
                        FROM call_events 
                        WHERE unique_id = $1 
                          AND event_type IN ('dial', 'start')
                          AND raw_data ? 'Trunk'
                        ORDER BY event_timestamp DESC
                        LIMIT 1
                    """
                    trunk_result = await connection.fetchrow(trunk_query, unique_id)
                    if trunk_result and trunk_result['trunk_data']:
                        trunk = str(trunk_result['trunk_data']).strip('"')
                        logging.info(f"Получили trunk '{trunk}' из события для {unique_id}")
                except Exception as e:
                    logging.error(f"Ошибка получения trunk для {unique_id}: {e}")
            
            # Парсинг времени
            start_time = None
            end_time = None
            duration = 0
            
            if start_time_str and end_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str)
                    end_time = datetime.fromisoformat(end_time_str)
                    duration = int((end_time - start_time).total_seconds())
                except:
                    pass
            
            # 🔗 Генерируем UUID ссылку для записи разговора (только если не передан)
            if uuid_token is None:
                uuid_token = str(uuid.uuid4())
            call_url = f"https://bot.vochi.by/recordings/file/{uuid_token}"
            
            # Создаем запись в calls с ПОЛНЫМИ данными включая UUID ссылку
            insert_query = """
                INSERT INTO calls (
                    unique_id, token, enterprise_id, phone_number, 
                    call_status, call_type, duration, data_source, created_at,
                    start_time, end_time, trunk, raw_data,
                    uuid_token, call_url
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (unique_id) DO UPDATE SET
                    start_time = COALESCE(EXCLUDED.start_time, calls.start_time),
                    end_time = COALESCE(EXCLUDED.end_time, calls.end_time),
                    trunk = COALESCE(EXCLUDED.trunk, calls.trunk),
                    call_status = EXCLUDED.call_status,
                    duration = EXCLUDED.duration,
                    raw_data = COALESCE(EXCLUDED.raw_data, calls.raw_data),
                    uuid_token = COALESCE(EXCLUDED.uuid_token, calls.uuid_token),
                    call_url = COALESCE(EXCLUDED.call_url, calls.call_url)
                RETURNING id
            """
            
            result = await connection.fetchrow(
                insert_query,
                unique_id, hashed_token, enterprise_id, phone_number,
                call_status, call_type, duration, 'live', datetime.now(),
                start_time, end_time, trunk, json.dumps(data),
                uuid_token, call_url
            )
            
            if result:
                call_id = result['id']
                logging.info(f"✅ Создана запись call_id={call_id} для {unique_id}")
                logging.info(f"🔗 UUID ссылка: {call_url}")
                
                # Помечаем событие как обработанное
                update_query = """
                    UPDATE call_events 
                    SET processed = true 
                    WHERE unique_id = $1 AND event_type = 'hangup'
                """
                await connection.execute(update_query, unique_id)
                
                # Возвращаем call_id и call_url для использования в Telegram сообщении
                return {"call_id": call_id, "call_url": call_url}
            else:
                logging.debug(f"Call record for {unique_id} already exists, skipping")
                return None
                
    except Exception as e:
        logging.error(f"Error creating call record for {unique_id}: {e}")
        return None

async def process_hangup(bot: Bot, chat_id: int, data: dict):
    """
    Модернизированный обработчик события 'hangup' (17.01.2025):
    - Использует новую систему группировки по номеру телефона
    - Отправляет финальные сообщения как комментарии к bridge событиям
    - Применяет правильные форматы из файла "Пояснение"
    - Различает успешные/неуспешные звонки по CallStatus
    """
    try:
        # Получаем номер для группировки событий
        phone_for_grouping = get_phone_for_grouping(data)

        # ───────── Шаг 1. Извлечение данных ─────────
        uid = data.get("UniqueId", "")
        # ВАЖНО: В старых HANGUP без ExternalInitiated нет CallerIDNum, используем Phone
        caller = data.get("CallerIDNum", "") or data.get("Phone", "") or ""
        exts = data.get("Extensions", []) or []
        connected = data.get("ConnectedLineNum", "") or ""
        call_status = int(data.get("CallStatus", -1))
        call_type = int(data.get("CallType", -1))
        token = data.get("Token", "")
        trunk_info = data.get("Trunk", "")
        
        # Получаем номер предприятия для метаданных из БД по токену
        enterprise_number = "0000"  # fallback логика
        if token:
            try:
                pool = await get_pool()
                if pool:
                    async with pool.acquire() as conn:
                        ent_row = await conn.fetchrow("SELECT number FROM enterprises WHERE name2 = $1", token)
                        if ent_row:
                            enterprise_number = ent_row["number"]
            except Exception as e:
                logging.warning(f"[process_hangup] Failed to get enterprise_number for token {token}: {e}")
                enterprise_number = token[:4] if len(token) >= 4 else "0000"

        logging.info(f"[process_hangup] RAW DATA = {data!r}")
        logging.info(f"[process_hangup] Phone for grouping: {phone_for_grouping}")
        logging.info(f"[process_hangup] Status: {call_status}, Type: {call_type}")
        logging.info(f"[process_hangup] DEBUG: caller='{caller}', exts={exts}, connected='{connected}'")

        # БЕЗОПАСНАЯ ПРОВЕРКА МАССИВОВ
        try:
            if exts and len(exts) > 0:
                logging.info(f"[process_hangup] DEBUG: exts[0] = '{exts[0]}'")
            else:
                logging.info(f"[process_hangup] DEBUG: exts is empty or None")
        except Exception as e:
            logging.error(f"[process_hangup] ERROR accessing exts: {e}, exts={exts}")
            exts = []  # Обнуляем если есть проблемы

        # 🆕 ВОССТАНОВЛЕНИЕ Extensions из dial_cache если Asterisk прислал пустые
        # Фильтруем пустые строки из exts (с преобразованием в str для безопасности)
        exts = [str(ext).strip() for ext in exts if ext and str(ext).strip()]
        
        if not exts and uid:
            # Пытаемся восстановить из dial_cache_by_chat
            chat_dial_cache = dial_cache_by_chat.get(chat_id, {})
            if uid in chat_dial_cache:
                cached_exts = chat_dial_cache[uid].get("extensions", [])
                if cached_exts:
                    exts = [str(ext).strip() for ext in cached_exts if ext and str(ext).strip()]
                    logging.info(f"[process_hangup] 🔄 Recovered extensions from dial_cache: {exts}")
            
            # Если не нашли в dial_cache - ищем в call_events (БД)
            if not exts:
                try:
                    pool = await get_pool()
                    if pool:
                        async with pool.acquire() as connection:
                            query = """
                                SELECT raw_data->'Extensions' as extensions
                                FROM call_events 
                                WHERE unique_id = $1 
                                  AND event_type = 'dial'
                                  AND raw_data ? 'Extensions'
                                ORDER BY event_timestamp DESC
                                LIMIT 1
                            """
                            result = await connection.fetchrow(query, uid)
                            if result and result['extensions']:
                                try:
                                    db_exts = json.loads(str(result['extensions']))
                                    exts = [str(ext).strip() for ext in db_exts if ext and str(ext).strip()]
                                    logging.info(f"[process_hangup] 🔄 Recovered extensions from call_events: {exts}")
                                except:
                                    pass
                except Exception as e:
                    logging.warning(f"[process_hangup] Failed to recover extensions from DB: {e}")

        # Создаем запись в таблице calls и получаем ссылку на запись
        call_record_info = None
        if uid and token:
            # Используем общий UUID токен если он есть (для одинаковых ссылок во всех chat_id)
            shared_uuid = data.get("_shared_uuid_token", None)
            call_record_info = await create_call_record(uid, token, data, shared_uuid)

        # ───────── Шаг 2. Очистка состояния системы ─────────
        bridge_store.pop(uid, None)
        dial_cache.pop(uid, None)
        active_bridges.pop(uid, None)

        # ───────── Шаг 3. Расчет длительности ─────────
        duration_text = ""
        actual_start_time_str = ""
        try:
            start_time_str = data.get("StartTime", "")
            # Fallback: если StartTime пустой, используем DateReceived
            if not start_time_str:
                start_time_str = data.get("DateReceived", "")
            actual_start_time_str = start_time_str  # Сохраняем для отображения
            end_time_str = data.get("EndTime", "")
            if start_time_str and end_time_str:
                start_time = datetime.fromisoformat(start_time_str)
                end_time = datetime.fromisoformat(end_time_str)
                total_seconds = int((end_time - start_time).total_seconds())
                duration_text = f"{total_seconds//60:02d}:{total_seconds%60:02d}"
        except Exception as e:
            logging.warning(f"[process_hangup] Failed to calculate duration: {e}")

        # ───────── Шаг 4. Определение типа звонка ─────────
        caller_is_internal = is_internal_number(caller)
        external_initiated = data.get("ExternalInitiated", False)
        
        # ВАЖНО: Если ExternalInitiated=true, то это ВСЕГДА внешний звонок (не внутренний)
        # Даже если caller и connected оба внутренние (промежуточные bridge)
        # ИСКЛЮЧЕНИЕ: настоящий внутренний звонок (Phone внутренний + Extensions внутренние + нет Trunk)
        if external_initiated:
            # ExternalInitiated=true + CallType=2 — проверяем, это настоящий внутренний или промежуточный
            if call_type == 2:
                # Проверяем: Phone внутренний И все Extensions внутренние → настоящий внутренний звонок
                phone_is_internal = is_internal_number(caller)
                exts_are_internal = exts and all(is_internal_number(ext) for ext in exts if ext)
                no_trunk = not trunk_info or trunk_info in ["", "unknown", "<unknown>"]
                
                if phone_is_internal and exts_are_internal:
                    # Это НАСТОЯЩИЙ внутренний звонок, НЕ пропускаем
                    logging.info(f"[process_hangup] INTERNAL CALL detected: Phone={caller}, Extensions={exts} - processing")
                    call_direction = "internal"
                    callee = exts[0] if exts else ""
                else:
                    # Это промежуточная нога внешнего звонка, ПРОПУСКАЕМ
                    logging.info(f"[process_hangup] Skipping intermediate hangup (ExternalInitiated=true, CallType=2) uid={uid}")
                    return {"status": "skipped", "reason": "intermediate_leg_hangup"}
            # Внешний звонок (определяем направление по CallType)
            elif call_type == 1:
                call_direction = "outgoing"
            elif call_type == 0:
                call_direction = "incoming"
            else:
                call_direction = "unknown"
        elif call_type == 2 or (caller_is_internal and connected and is_internal_number(connected)):
            # Внутренний звонок (только если НЕ ExternalInitiated)
            call_direction = "internal"
            callee = connected or (exts[0] if exts and len(exts) > 0 else "")
        else:
            # Внешние звонки (без ExternalInitiated, определяем по CallType)
            if call_type == 1:
                call_direction = "outgoing"
            elif call_type == 0:
                call_direction = "incoming"
            else:
                call_direction = "unknown"
        
        # ───────── Шаг 5. Получаем обогащённые метаданные ─────────
        # Извлекаем данные для обогащения
        line_id = extract_line_id_from_exten(trunk_info)  # ID линии из Trunk
        internal_phone = None
        external_phone = None
        
        # Инициализируем переменные для кнопок (используются для всех типов звонков)
        user_internal_phones = []
        owner_chat_id = None
        enterprise_secret = None
        clean_phone = None
        
        # Определяем внутренний и внешний номера в зависимости от типа звонка
        if call_direction == "incoming":
            external_phone = caller
            if connected and is_internal_number(connected):
                internal_phone = connected
            elif exts:
                for ext in reversed(exts):
                    if is_internal_number(ext):
                        internal_phone = ext
                        break
        elif call_direction == "outgoing":
            # ИСПРАВЛЕНО: Используем ту же логику что и в dial.py
            external_phone = data.get("Phone", "")  # Внешний номер из Phone
            
            # Ищем внутренний номер в Extensions (как в dial.py)
            if exts:
                for ext in exts:
                    if ext and is_internal_number(ext):  # Проверяем что ext не пустой
                        internal_phone = ext
                        break
            
            # Если не нашли в Extensions, проверяем CallerIDNum
            if not internal_phone:
                caller_id = data.get("CallerIDNum", "")
                if is_internal_number(caller_id):
                    internal_phone = caller_id
            
            # ДОПОЛНИТЕЛЬНО: Если все еще не нашли, ищем в call_events текущего звонка
            if not internal_phone:
                try:
                    pool = await get_pool()
                    if pool:
                        async with pool.acquire() as connection:
                            # Ищем в событиях dial/bridge для этого звонка
                            # Приоритет: сначала dial, потом bridge
                            query = """
                                SELECT 
                                    value->'event_data'->'Extensions' as extensions,
                                    value->'event_data'->>'CallerIDNum' as caller_id
                                FROM call_traces, 
                                     jsonb_array_elements(call_events) as value
                                WHERE enterprise_number = $1
                                  AND (unique_id = $2 OR related_unique_ids @> jsonb_build_array($2))
                                  AND value->>'event_type' = 'dial'
                                ORDER BY value->>'event_timestamp' ASC
                                LIMIT 1
                            """
                            result = await connection.fetchrow(query, enterprise_number, uid)
                            if result:
                                # Пробуем Extensions
                                if result['extensions']:
                                    try:
                                        extensions = json.loads(str(result['extensions']))
                                        for ext in extensions:
                                            if ext and is_internal_number(str(ext)):
                                                internal_phone = str(ext)
                                                logging.info(f"[process_hangup] Found internal_phone '{internal_phone}' from call_events Extensions")
                                                break
                                    except:
                                        pass
                                
                                # Если не нашли, пробуем CallerIDNum
                                if not internal_phone and result['caller_id']:
                                    if is_internal_number(result['caller_id']):
                                        internal_phone = result['caller_id']
                                        logging.info(f"[process_hangup] Found internal_phone '{internal_phone}' from call_events CallerIDNum")
                except Exception as e:
                    logging.error(f"[process_hangup] Error searching for internal phone in call_events: {e}")
            
            # ДОПОЛНИТЕЛЬНО для паттерна 1-2: ищем внутренний номер в предыдущих событиях
            if not internal_phone and data.get("ExternalInitiated"):
                try:
                    # Ищем внутренний номер из предыдущих bridge событий для этого же внешнего номера
                    pool = await get_pool()
                    if pool:
                        async with pool.acquire() as connection:
                            # Ищем последний bridge с тем же внешним номером
                            query = """
                                SELECT raw_data->'ConnectedLineNum' as internal_num
                                FROM call_events 
                                WHERE raw_data->>'Token' = $1 
                                  AND event_type = 'bridge'
                                  AND (raw_data->>'CallerIDNum' = $2 OR raw_data->>'ConnectedLineNum' = $2)
                                  AND raw_data ? 'ConnectedLineNum'
                                ORDER BY event_timestamp DESC
                                LIMIT 1
                            """
                            result = await connection.fetchrow(query, token, external_phone)
                            if result and result['internal_num']:
                                potential_internal = str(result['internal_num']).strip('"')
                                if is_internal_number(potential_internal):
                                    internal_phone = potential_internal
                                    logging.info(f"[process_hangup] Found internal_phone '{internal_phone}' from previous bridge event")
                except Exception as e:
                    logging.error(f"[process_hangup] Error searching for internal phone in DB: {e}")
        elif call_direction == "internal":
            # Для внутренних звонков оба номера внутренние
            internal_phone = caller if is_internal_number(caller) else None
        
        # ───────── Используем pre-enriched данные (уже сделано в main.py) ─────────
        enriched_data = data.get("_enriched_data", {})
        
        # Используем pre-computed параметры из main.py (если есть)
        if "_internal_phone" in data:
            internal_phone = data["_internal_phone"]
        if "_external_phone" in data:
            external_phone = data["_external_phone"]
        if "_line_id" in data:
            line_id = data["_line_id"]
        
        if enriched_data:
            logging.info(f"[process_hangup] Using pre-enriched data: {enriched_data}")
        else:
            logging.warning(f"[process_hangup] No pre-enriched data available, will enrich now")
            # Fallback - если по какой-то причине не было pre-enriched
            enriched_data = await metadata_client.enrich_message_data(
                enterprise_number=enterprise_number,
                internal_phone=internal_phone,
                external_phone=external_phone,
                line_id=line_id,
                short_names=False
            )
        
        # ───────── Шаг 6. Формируем текст согласно Пояснению ─────────
        
        if call_direction == "internal":
            # Внутренние звонки - БЕЗ кнопки "Детали звонка" и БЕЗ логирования
            # enterprise_secret НЕ получаем - кнопка не нужна
            
            # Определяем получателя: используем callee (уже определён выше) или exts[0]
            receiver = callee or (exts[0] if exts else "") or connected
            
            # Получаем ФИО обоих участников параллельно
            try:
                caller_name, receiver_name = await asyncio.gather(
                    metadata_client.get_manager_name(enterprise_number, caller, short=False),
                    metadata_client.get_manager_name(enterprise_number, receiver, short=False)
                )
                
                # Форматируем: если есть ФИО - "ФИО (номер)", иначе просто номер
                if caller_name and not caller_name.startswith("Доб."):
                    caller_display = f"{caller_name} ({caller})"
                else:
                    caller_display = caller
                    
                if receiver_name and not receiver_name.startswith("Доб."):
                    connected_display = f"{receiver_name} ({receiver})"
                else:
                    connected_display = receiver
            except Exception as e:
                logging.warning(f"[process_hangup] Failed to get manager names for internal call: {e}")
                caller_display = caller
                connected_display = receiver
            
            if call_status == 2:
                # Успешный внутренний звонок
                text = (f"✅Успешный внутренний звонок\n"
                       f"☎️{caller_display}➡️\n"
                       f"☎️{connected_display}")
                # Используем actual_start_time_str (StartTime или DateReceived)
                if actual_start_time_str:
                    try:
                        if 'T' in actual_start_time_str:
                            time_part = actual_start_time_str.split('T')[1][:5]
                        elif ' ' in actual_start_time_str:
                            parts = actual_start_time_str.split(' ')
                            if len(parts) >= 2:
                                time_part = parts[1][:5]
                            else:
                                time_part = "неизв"
                        else:
                            time_part = "неизв"
                        text += f"\n⏰Начало звонка {time_part}"
                    except Exception as e:
                        logging.warning(f"[process_hangup] Error parsing StartTime '{actual_start_time_str}': {e}")
                        text += f"\n⏰Начало звонка неизв"
                if duration_text:
                    text += f"\n⌛ Длительность: {duration_text}"
                text += get_recording_link_text(call_record_info)
            else:
                # Неуспешный внутренний звонок
                text = (f"❌ Коллега не поднял трубку\n"
                       f"☎️{caller_display}➡️\n" 
                       f"☎️{connected_display}")
                # Используем actual_start_time_str (StartTime или DateReceived)
                if actual_start_time_str:
                    try:
                        if 'T' in actual_start_time_str:
                            time_part = actual_start_time_str.split('T')[1][:5]
                        elif ' ' in actual_start_time_str:
                            parts = actual_start_time_str.split(' ')
                            if len(parts) >= 2:
                                time_part = parts[1][:5]
                            else:
                                time_part = "неизв"
                        else:
                            time_part = "неизв"
                        text += f"\n⏰Начало звонка {time_part}"
                    except Exception as e:
                        logging.warning(f"[process_hangup] Error parsing StartTime '{actual_start_time_str}': {e}")
                        text += f"\n⏰Начало звонка неизв"
                if duration_text:
                    text += f"\n⌛ Дозванивался: {duration_text}"
        
        elif call_direction == "incoming":
            # Входящие звонки
            # Используем _external_phone (уже обработан в main.py) или Phone или CallerIDNum
            external_phone = data.get("_external_phone") or data.get("Phone") or caller
            phone = format_phone_number(external_phone)
            display = phone if not phone.startswith("+000") else "Номер не определен"
            
            # Получаем ВСЕ внутренние номера для текущего chat_id
            try:
                # Получаем chat_id владельца бота и secret предприятия
                owner_chat_id = await get_bot_owner_chat_id(token)
                enterprise_secret = await get_enterprise_secret(token)
                
                # Если текущий chat_id НЕ владелец - получаем ВСЕ его внутренние номера
                if owner_chat_id != chat_id:
                    user_internal_phones = await get_all_internal_phones_by_tg_id(
                        enterprise_number=enterprise_number,
                        telegram_tg_id=chat_id
                    )
                    logging.info(
                        f"[process_hangup] User internal phones for chat_id={chat_id}: {user_internal_phones}"
                    )
                else:
                    logging.info(
                        f"[process_hangup] chat_id={chat_id} is bot owner, no callback buttons"
                    )
            except Exception as e:
                logging.error(f"[process_hangup] Error getting user internal phones: {e}")
            
            # Очищаем external_phone от лишних символов для callback data
            if user_internal_phones and enterprise_secret:
                clean_phone = external_phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            
            # Обогащаем номер клиента именем если есть
            if enriched_data.get("customer_name"):
                display = f"{display} ({enriched_data['customer_name']})"
            
            if call_status == 2:
                # Успешный входящий звонок
                text = f"✅Успешный входящий звонок\n💰{display}"
                
                # Добавляем информацию о менеджере (обогащённую)
                if internal_phone:
                    manager_fio = enriched_data.get("manager_name", "")
                    # ИСПРАВЛЕНО: Единообразие - если ФИО есть и это не "Доб.XXX", показываем "ФИО (номер)", иначе просто номер
                    if manager_fio and not manager_fio.startswith("Доб."):
                        manager_display = f"{manager_fio} ({internal_phone})"
                    else:
                        manager_display = internal_phone
                    text += f"\n☎️{manager_display}"
                elif connected and is_internal_number(connected):
                    text += f"\n☎️{connected}"
                elif exts:
                    # Если есть расширения, берем последнее внутреннее
                    for ext in reversed(exts):
                        if is_internal_number(ext):
                            text += f"\n☎️{ext}"
                            break
                            
                # Добавляем информацию о линии (обогащённую)
                if enriched_data.get("line_name"):
                    text += f"\n📡{enriched_data['line_name']}"
                elif trunk_info:
                    text += f"\nЛиния: {trunk_info}"
                    
                # Добавляем время и длительность  
                if data.get('StartTime'):
                    start_time = data.get('StartTime')
                    try:
                        if 'T' in start_time:
                            time_part = start_time.split('T')[1][:5]
                        elif ' ' in start_time:
                            # Формат "2025-07-17 15:39:04"
                            parts = start_time.split(' ')
                            if len(parts) >= 2:
                                time_part = parts[1][:5]  # Берем первые 5 символов времени
                            else:
                                time_part = "неизв"
                        else:
                            time_part = "неизв"
                        text += f"\n⏰Начало звонка {time_part}"
                    except Exception as e:
                        logging.warning(f"[process_hangup] Error parsing StartTime '{start_time}': {e}")
                        text += f"\n⏰Начало звонка неизв"
                if duration_text:
                    text += f"\n⌛ Длительность: {duration_text}"
                    text += get_recording_link_text(call_record_info)
            else:
                # Неуспешный входящий звонок
                text = f"❌ Мы не подняли трубку\n💰{display}"
                
                # Добавляем всех, кому звонили (со спойлером "Менеджеры:" как в download.py)
                if exts:
                    internal_exts = [ext for ext in exts if is_internal_number(ext)]
                    mobile_exts = [ext for ext in exts if not is_internal_number(ext)]
                    
                    if internal_exts:
                        # Получаем ФИО всех менеджеров параллельно
                        try:
                            manager_names = await asyncio.gather(*[
                                metadata_client.get_manager_name(enterprise_number, ext, short=False)
                                for ext in internal_exts
                            ], return_exceptions=True)
                            
                            # Формируем список менеджеров
                            managers_lines = []
                            for ext, name in zip(internal_exts, manager_names):
                                if isinstance(name, Exception) or not name or name.startswith("Доб."):
                                    managers_lines.append(f"☎️{ext}")
                                else:
                                    managers_lines.append(f"☎️{name} ({ext})")
                            
                            # Если несколько менеджеров - в спойлер, если один - просто строка
                            if len(managers_lines) > 1:
                                # Expandable blockquote со спойлером
                                managers_list = "👨🏼‍💼Менеджеры:\n\n" + "\n".join(managers_lines)
                                text += f"\n<blockquote expandable>{managers_list}</blockquote>"
                            else:
                                # Один менеджер - без спойлера
                                text += f"\n{managers_lines[0]}"
                        except Exception as e:
                            logging.warning(f"[process_hangup] Failed to get manager names: {e}")
                            # Fallback - просто номера
                            for ext in internal_exts:
                                text += f"\n☎️{ext}"
                    
                    # Мобильные номера добавляем отдельно
                    for ext in mobile_exts:
                        text += f"\n📱{format_phone_number(ext)}"
                
                # Добавляем информацию о линии (обогащённую)
                if enriched_data.get("line_name"):
                    text += f"\n📡{enriched_data['line_name']}"
                elif trunk_info:
                    text += f"\nЛиния: {trunk_info}"
                    
                # Добавляем время дозвона
                if data.get('StartTime'):
                    start_time_str = data.get('StartTime')
                    try:
                        if 'T' in start_time_str:
                            time_part = start_time_str.split('T')[1][:5]
                        else:
                            time_part = start_time_str.split(' ')[1][:5] if ' ' in start_time_str else start_time_str[-5:]
                        text += f"\n⏰Начало звонка {time_part}"
                    except:
                        text += f"\n⏰Начало звонка {start_time_str}"
                if duration_text:
                    text += f"\n⌛ Дозванивался: {duration_text}"
        
        elif call_direction == "outgoing":
            # Исходящие звонки  
            # ИСПРАВЛЕНО: Улучшенное определение кому звонили
            external_phone = ""
            internal_caller = ""
            
            # Определяем внешний номер (кому звонили)
            if connected and not is_internal_number(connected):
                external_phone = connected
            elif exts:
                # Ищем внешний номер среди Extensions
                for ext in exts:
                    if not is_internal_number(ext):
                        external_phone = ext
                        break
            
            # Определяем внутреннего звонящего
            if caller and is_internal_number(caller):
                internal_caller = caller
            elif exts:
                # Ищем внутренний номер среди Extensions
                for ext in exts:
                    if is_internal_number(ext):
                        internal_caller = ext
                        break
            
            # FALLBACK: Если не нашли internal_caller, ищем в dial_cache
            if not internal_caller:
                chat_dial_cache = dial_cache_by_chat.get(chat_id, {})
                if uid in chat_dial_cache:
                    cached_exts = chat_dial_cache[uid].get("extensions", [])
                    for ext in cached_exts:
                        if ext and is_internal_number(ext):
                            internal_caller = ext
                            logging.info(f"[HANGUP] Found internal_caller={ext} from dial_cache")
                            break
            
            # Если не нашли внешний номер, используем данные из события
            if not external_phone:
                external_phone = data.get("Phone", "") or data.get("ConnectedLineNum", "") or ""
                
            phone = format_phone_number(external_phone)
            display = phone if not phone.startswith("+000") else "Номер не определен"
            
            # Получаем ВСЕ внутренние номера для текущего chat_id
            try:
                # Получаем chat_id владельца бота и secret предприятия
                owner_chat_id = await get_bot_owner_chat_id(token)
                enterprise_secret = await get_enterprise_secret(token)
                
                # Если текущий chat_id НЕ владелец - получаем ВСЕ его внутренние номера
                if owner_chat_id != chat_id:
                    user_internal_phones = await get_all_internal_phones_by_tg_id(
                        enterprise_number=enterprise_number,
                        telegram_tg_id=chat_id
                    )
                    logging.info(
                        f"[process_hangup] User internal phones for chat_id={chat_id}: {user_internal_phones}"
                    )
                else:
                    logging.info(
                        f"[process_hangup] chat_id={chat_id} is bot owner, no callback buttons"
                    )
            except Exception as e:
                logging.error(f"[process_hangup] Error getting user internal phones: {e}")
            
            # Очищаем external_phone от лишних символов для callback data
            if user_internal_phones and enterprise_secret:
                clean_phone = external_phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            
            # Обогащаем номер клиента именем если есть
            if enriched_data.get("customer_name"):
                display = f"{display} ({enriched_data['customer_name']})"
            
            if call_status == 2:
                # Успешный исходящий звонок
                text = f"✅Успешный исходящий звонок"
                
                # Добавляем информацию о менеджере (обогащённую)
                # ИСПРАВЛЕНО: Используем internal_phone вместо internal_caller для паттерна 1-2
                # Если internal_phone не найден, пытаемся извлечь из enriched_data
                manager_number = internal_caller or internal_phone
                if not manager_number and enriched_data.get("manager_name"):
                    # Пытаемся извлечь номер из ФИО вида "Копачёв Алексей (151)"
                    import re
                    match = re.search(r'\((\d+)\)', enriched_data.get("manager_name", ""))
                    if match:
                        manager_number = match.group(1)
                
                if manager_number:
                    manager_fio = enriched_data.get("manager_name", "")
                    # ИСПРАВЛЕНО: Единообразие - если ФИО есть и это не "Доб.XXX", показываем "ФИО (номер)", иначе просто номер
                    if manager_fio and not manager_fio.startswith("Доб."):
                        manager_display = f"{manager_fio} ({manager_number})"
                    else:
                        manager_display = manager_number
                    text += f"\n☎️{manager_display}"
                
                text += f"\n💰{display}"
                
                # Добавляем информацию о линии (обогащённую)
                if enriched_data.get("line_name"):
                    text += f"\n📡{enriched_data['line_name']}"
                elif trunk_info:
                    text += f"\nЛиния: {trunk_info}"
                    
                # Добавляем время начала с безопасной обработкой
                if data.get('StartTime'):
                    start_time_str = data.get('StartTime')
                    try:
                        if 'T' in start_time_str:
                            time_part = start_time_str.split('T')[1][:5]
                        else:
                            time_part = start_time_str.split(' ')[1][:5] if ' ' in start_time_str else start_time_str[-5:]
                        text += f"\n⏰Начало звонка {time_part}"
                    except:
                        text += f"\n⏰Начало звонка {start_time_str}"
                if duration_text:
                    text += f"\n⌛ Длительность: {duration_text}"
                    text += get_recording_link_text(call_record_info)
            else:
                # Неуспешный исходящий звонок
                text = f"❌ Абонент не поднял трубку"
                
                # Добавляем информацию о менеджере (обогащённую)
                # ИСПРАВЛЕНО: Используем internal_phone вместо internal_caller для паттерна 1-2
                manager_number = internal_caller or internal_phone
                if manager_number:
                    manager_fio = enriched_data.get("manager_name", "")
                    # ИСПРАВЛЕНО: Единообразие - если ФИО есть и это не "Доб.XXX", показываем "ФИО (номер)", иначе просто номер
                    if manager_fio and not manager_fio.startswith("Доб."):
                        manager_display = f"{manager_fio} ({manager_number})"
                    else:
                        manager_display = manager_number
                    text += f"\n☎️{manager_display}"
                
                text += f"\n💰{display}"
                
                # Добавляем информацию о линии (обогащённую)
                if enriched_data.get("line_name"):
                    text += f"\n📡{enriched_data['line_name']}"
                elif trunk_info:
                    text += f"\nЛиния: {trunk_info}"
                    
                # Добавляем время дозвона  
                if data.get('StartTime'):
                    start_time_str = data.get('StartTime')
                    try:
                        if 'T' in start_time_str:
                            time_part = start_time_str.split('T')[1][:5]
                        else:
                            time_part = start_time_str.split(' ')[1][:5] if ' ' in start_time_str else start_time_str[-5:]
                        text += f"\n⏰Начало звонка {time_part}"
                    except:
                        text += f"\n⏰Начало звонка {start_time_str}"
                if duration_text:
                    text += f"\n⌛ Дозванивался: {duration_text}"
        
        else:
            # Неопределенный тип - базовый формат
            text = f"❌ Завершённый звонок\n💰{format_phone_number(caller)}"
            if duration_text:
                text += f"\n⌛ {duration_text}"

        # НЕ экранируем html-теги т.к. используем parse_mode="HTML"
        # и нужны кликабельные ссылки на записи
        safe_text = text
        logging.info(f"[process_hangup] => chat={chat_id}, text={safe_text!r}")

        # ───────── Шаг 6. Проверяем, нужно ли отправить как комментарий ─────────
        should_comment, reply_to_id = should_send_as_comment(phone_for_grouping, 'hangup', chat_id)

        # ───────── Шаг 7. Отправляем финальное сообщение ПЕРЕД удалением bridge ─────────
        logging.info(f"[process_hangup] === SENDING HANGUP MESSAGE ===")
        logging.info(f"[process_hangup] should_comment={should_comment}, reply_to_id={reply_to_id}")
        logging.info(f"[process_hangup] chat_id={chat_id}, safe_text={safe_text!r}")
        
        # Создаём Inline кнопки для звонка (только для менеджеров, не для владельца)
        reply_markup = None
        buttons = []
        
        # Кнопки "Позвонить" (только если есть внутренние номера и телефон клиента)
        if user_internal_phones and enterprise_secret and clean_phone:
            # python-telegram-bot синтаксис (не aiogram!)
            for internal_phone in user_internal_phones:
                button = InlineKeyboardButton(
                    text=f"📞 Позвонить с {internal_phone}",
                    callback_data=f"call:{clean_phone}:{internal_phone}:{enterprise_secret}"
                )
                buttons.append([button])  # Каждая кнопка на отдельной строке
            
            logging.info(
                f"[process_hangup] Added {len(user_internal_phones)} call button(s) "
                f"for internal_phones={user_internal_phones}"
            )
        
        # Кнопка "Детали звонка" (для ВСЕХ пользователей, включая владельца)
        if enterprise_secret and uid:
            details_url = f"https://bot.vochi.by/call/{enterprise_number}/{uid}?token={enterprise_secret}"
            details_button = InlineKeyboardButton(
                text="📊 Детали звонка",
                url=details_url
            )
            buttons.append([details_button])
            logging.info(f"[process_hangup] Added call details button: {details_url}")
        
        # Создаём keyboard если есть хотя бы одна кнопка
        if buttons:
            keyboard = InlineKeyboardMarkup(buttons)
            reply_markup = keyboard
        
        try:
            ent_num = data.get("_enterprise_number", enterprise_number)
            # Формируем текст для лога с URL деталей звонка
            log_text = safe_text
            if enterprise_secret and uid:
                log_text = f"{safe_text} | URL: {details_url}"
            
            if should_comment and reply_to_id:
                logging.info(f"[process_hangup] Sending as comment to message {reply_to_id}")
                try:
                    sent = await bot.send_message(
                        chat_id,
                        safe_text,
                        reply_to_message_id=reply_to_id,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=reply_markup
                    )
                    # Добавляем message_id к сообщению для отладки
                    debug_text = f"{safe_text}\n🔖 msg:{sent.message_id}"
                    try:
                        await bot.edit_message_text(debug_text, chat_id, sent.message_id, parse_mode="HTML", disable_web_page_preview=True, reply_markup=reply_markup)
                    except Exception as e:
                        logging.warning(f"[process_hangup] Failed to add message_id to text: {e}")
                    log_telegram_event(ent_num, "send", chat_id, "hangup", sent.message_id, uid, debug_text if 'debug_text' in dir() else log_text)
                    logging.info(f"[process_hangup] ✅ HANGUP COMMENT SENT: message_id={sent.message_id}")
                except BadRequest as e:
                    # Если reply не удался (сообщение удалено), отправляем без reply
                    logging.warning(f"[process_hangup] Reply failed: {e}, sending without reply")
                    sent = await bot.send_message(
                        chat_id, 
                        safe_text, 
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=reply_markup
                    )
                    # Добавляем message_id к сообщению для отладки
                    debug_text = f"{safe_text}\n🔖 msg:{sent.message_id}"
                    try:
                        await bot.edit_message_text(debug_text, chat_id, sent.message_id, parse_mode="HTML", disable_web_page_preview=True, reply_markup=reply_markup)
                    except Exception as e:
                        logging.warning(f"[process_hangup] Failed to add message_id to text: {e}")
                    log_telegram_event(ent_num, "send", chat_id, "hangup", sent.message_id, uid, debug_text if 'debug_text' in dir() else log_text)
                    logging.info(f"[process_hangup] ✅ HANGUP MESSAGE SENT (no reply): message_id={sent.message_id}")
            else:
                logging.info(f"[process_hangup] Sending as standalone message")
                sent = await bot.send_message(
                    chat_id, 
                    safe_text, 
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=reply_markup
                )
                # Добавляем message_id к сообщению для отладки
                debug_text = f"{safe_text}\n🔖 msg:{sent.message_id}"
                try:
                    await bot.edit_message_text(debug_text, chat_id, sent.message_id, parse_mode="HTML", disable_web_page_preview=True, reply_markup=reply_markup)
                except Exception as e:
                    logging.warning(f"[process_hangup] Failed to add message_id to text: {e}")
                log_telegram_event(ent_num, "send", chat_id, "hangup", sent.message_id, uid, debug_text if 'debug_text' in dir() else log_text)
                logging.info(f"[process_hangup] ✅ HANGUP MESSAGE SENT: message_id={sent.message_id}")
                
        except BadRequest as e:
            logging.error(f"[process_hangup] ❌ send_message failed: {e}. text={safe_text!r}")
            # НЕ ВОЗВРАЩАЕМ ОШИБКУ - ПРОДОЛЖАЕМ УДАЛЯТЬ ПРЕДЫДУЩИЕ СООБЩЕНИЯ!
            sent = None
        
        # 📝 Записываем timestamp hangup для оптимизации переотправки bridge
        # Ключ: (chat_id, enterprise_number) — чтобы разные юниты не влияли друг на друга
        import time
        hangup_key = (chat_id, ent_num)
        last_hangup_time_by_chat_enterprise[hangup_key] = time.time()
        logging.debug(f"[process_hangup] Updated last_hangup_time for {hangup_key}")
        
        # ───────── Шаг 8. HANGUP - ГЛАВНЫЙ КИЛЛЕР (удаляет ВСЁ: start/dial/bridge) ─────────
        # АТОМАРНЫЙ подход: DELETE возвращает message_id для удаления из TG
        phone = get_phone_for_grouping(data)
        
        logging.info(f"[HANGUP] 🔍 START for {phone}:{chat_id}")
        
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                url = f"http://localhost:8020/telegram/messages/{phone}/{chat_id}"
                
                # АТОМАРНОЕ удаление - получаем message_id которые нужно удалить из TG
                logging.info(f"[HANGUP] 🗑️ DELETE {url}")
                try:
                    resp = await client.delete(url)  # Удаляет ВСЁ
                    logging.info(f"[HANGUP] 📥 DELETE status={resp.status_code}")
                    logging.info(f"[HANGUP] 📥 DELETE body={resp.text}")
                    
                    if resp.status_code == 200:
                        delete_result = resp.json()
                        deleted_messages = delete_result.get("deleted_messages", {})
                        logging.info(f"[HANGUP] ✅ Got deleted_messages: {deleted_messages}")
                        
                        # Удаляем из TG ВСЕ message_id которые вернул DELETE
                        ent_num = data.get("_enterprise_number", enterprise_number)
                        for event_type, msg_ids in deleted_messages.items():
                            for msg_id in msg_ids:
                                logging.info(f"[HANGUP] 🗑️ Deleting {event_type.upper()} msg={msg_id}")
                                try:
                                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                                    log_telegram_event(ent_num, "delete", chat_id, event_type, msg_id, uid, "")
                                    logging.info(f"[HANGUP] ✅ {event_type.upper()} msg:{msg_id} deleted")
                                except BadRequest as e:
                                    logging.debug(f"[HANGUP] ⚠️ {event_type.upper()} msg:{msg_id} already deleted: {e}")
                                except Exception as e:
                                    logging.debug(f"[HANGUP] ⚠️ {event_type.upper()} msg:{msg_id} delete failed: {e}")
                    else:
                        logging.info(f"[HANGUP] ℹ️ No prev messages (status={resp.status_code})")
                except Exception as e:
                    logging.error(f"[HANGUP] ❌ Error: {e}")
        except Exception as e:
            logging.error(f"[HANGUP] ❌ Cache service error: {e}")
        
        # Старая логика для совместимости
        bridge_messages_to_delete = []
        
        # ИСПРАВЛЕНО: Используем правильные индивидуальные хранилища для chat_id
        from .utils import bridge_store_by_chat, phone_message_tracker_by_chat
        
        # 1. Проверяем bridge_store по UniqueId для конкретного chat_id
        chat_bridge_store = bridge_store_by_chat[chat_id]
        if uid in chat_bridge_store:
            bridge_msg = chat_bridge_store.pop(uid)
            bridge_messages_to_delete.append(bridge_msg)
            logging.info(f"[process_hangup] Found bridge message {bridge_msg} in bridge_store for uid {uid}")
            
            # ⏹️ Останавливаем фоновую задачу переотправки bridge
            stop_bridge_resend_task(chat_id, uid)
        
        # 🧹 CLEANUP: Проверяем bridge_by_internal - удаляем "зависшие" bridge по internal_number
        # Это нужно когда hangup приходит через download или в нештатных ситуациях
        internal_for_cleanup = exts[0] if exts and exts[0] else None
        if internal_for_cleanup and ent_num:
            bridge_key = (chat_id, ent_num, str(internal_for_cleanup))
            if bridge_key in bridge_by_internal:
                orphan_data = bridge_by_internal.pop(bridge_key)
                orphan_uid = orphan_data.get("uid", "")
                orphan_msg_id = orphan_data.get("message_id")
                
                # Если это НЕ тот же bridge что мы уже нашли - добавляем к удалению
                if orphan_uid != uid and orphan_msg_id and orphan_msg_id not in bridge_messages_to_delete:
                    bridge_messages_to_delete.append(orphan_msg_id)
                    logging.info(f"[process_hangup] 🧹 Found orphan bridge by internal={internal_for_cleanup}: uid={orphan_uid}, msg={orphan_msg_id}")
                    
                    # Останавливаем задачу переотправки для orphan bridge
                    stop_bridge_resend_task(chat_id, orphan_uid)
                    
                    # Удаляем из bridge_store_by_chat если есть
                    if orphan_uid in chat_bridge_store:
                        chat_bridge_store.pop(orphan_uid, None)
        
        # 2. ГЛАВНОЕ: Проверяем phone_message_tracker по ВНЕШНЕМУ НОМЕРУ (правильный якорь!)
        chat_phone_tracker = phone_message_tracker_by_chat[chat_id]
        if phone_for_grouping in chat_phone_tracker:
            tracker_data = chat_phone_tracker[phone_for_grouping]
            # Проверяем что tracker_data это словарь
            if isinstance(tracker_data, dict) and tracker_data.get('event_type') == 'bridge':
                bridge_msg_id = tracker_data['message_id']
                bridge_messages_to_delete.append(bridge_msg_id)
                # Очищаем tracker
                del chat_phone_tracker[phone_for_grouping]
                logging.info(f"[process_hangup] Found bridge message {bridge_msg_id} in phone_tracker for phone {phone_for_grouping}")
        
        # 3. Удаляем все найденные bridge сообщения
        ent_num = data.get("_enterprise_number", enterprise_number)
        for bridge_msg_id in bridge_messages_to_delete:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=bridge_msg_id)
                log_telegram_event(ent_num, "delete", chat_id, "bridge", bridge_msg_id, uid, "")
                logging.info(f"[process_hangup] Deleted bridge message {bridge_msg_id} due to hangup")
            except BadRequest as e:
                logging.warning(f"[process_hangup] Could not delete bridge message {bridge_msg_id}: {e}")
            except Exception as e:
                logging.error(f"[process_hangup] Error deleting bridge message {bridge_msg_id}: {e}")

        logging.info(f"[process_hangup] Deleted {len(bridge_messages_to_delete)} bridge messages")

        # ───────── Шаг 9. Обновляем состояние системы ─────────
        # Определяем callee для обратной совместимости
        if call_direction == "internal":
            callee = connected or ""
            is_int = True
        else:
            # Для внешних звонков используем первое расширение из списка, если есть
            if exts and len(exts) > 0:
                callee = exts[0]
            elif connected:
                callee = connected
            else:
                callee = ""
            is_int = False
        
        # ───────── Шаг 9. Обновляем состояние системы (только если сообщение отправлено) ─────────
        if sent:
            update_call_pair_message(caller, callee, sent.message_id, is_int, chat_id)
            update_hangup_message_map(caller, callee, sent.message_id, is_int, call_status, call_type, exts, chat_id=chat_id)
            
            # Обновляем новый трекер для группировки
            update_phone_tracker(phone_for_grouping, sent.message_id, 'hangup', data, chat_id)

            # ───────── Шаг 10. Сохраняем в БД ─────────
            await save_telegram_message(
                sent.message_id,
                "hangup",
                token,
                caller,
                callee,
                is_int
            )
            
        # ───────── Шаг 11. Уведомление U‑ON через 8020 (реальный звонок завершён) ─────────
        try:
            ext_for_notify = exts[0] if exts else (connected or "")
            notify_payload = {
                "enterprise_number": token,
                "phone": caller,
                "extension": ext_for_notify,
            }
            # Уведомления отключены для устранения блокировок
            pass
        except Exception as e:
            logging.warning(f"[process_hangup] notify incoming failed: {e}")

        if sent:
            logging.info(f"[process_hangup] Successfully sent hangup message {sent.message_id} for {phone_for_grouping}")
        else:
            logging.warning(f"[process_hangup] Hangup message was not sent for {phone_for_grouping}")
            # Возвращаем ошибку если сообщение не было отправлено
            return {"status": "error", "error": "Message was not sent"}

        # ───────── Fire-and-forget обновление customers ─────────
        try:
            asyncio.create_task(upsert_customer_from_hangup(data))
        except Exception:
            pass

        # 🔄 УНИВЕРСАЛЬНОЕ ОБОГАЩЕНИЕ ПРОФИЛЯ ЧЕРЕЗ 8020
        async def _enrich_and_edit(data: dict):
            """Универсальная задача для обогащения профиля клиента"""
            try:
                logging.info(f"[hangup] _enrich_and_edit called with data: {data}")
                
                # Получаем enterprise_number из токена
                pool = await get_pool()
                if not pool:
                    return
                    
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT number FROM enterprises WHERE name2 = $1 OR secret = $1 OR number = $1 LIMIT 1",
                        data.get("Token", "")
                    )
                    if not row:
                        return
                    current_enterprise_number = row["number"]
                
                # Определяем внешний номер для профиля
                phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
                if not phone:
                    return
                
                # Нормализуем номер в E.164 формат
                if not phone.startswith("+"):
                    phone_e164 = "+" + ''.join(ch for ch in phone if ch.isdigit())
                else:
                    phone_e164 = phone

                # Обогащение клиентских данных отключено для устранения блокировок
                pass

            except Exception as e:
                logging.error(f"[hangup] Error in _enrich_and_edit: {e}")

        try:
            logging.info(f"[hangup] Starting profile enrichment task for {uid}")
            await _enrich_and_edit(data)
        except Exception as e:
            logging.warning(f"[hangup] Failed to create enrichment task: {e}")

        # ───────── Fire-and-forget отправка в Integration Gateway (8020) ─────────
        try:
            token_for_gateway = token
            unique_id_for_gateway = uid
            event_type_for_gateway = "hangup"
            record_url_for_gateway = (call_record_info or {}).get("call_url")

            # Gateway dispatch отключен для устранения блокировок
            pass
        except Exception as e:
            logging.warning(f"[process_hangup] failed to schedule gateway dispatch: {e}")
        # Уведомления U‑ON и прочих провайдеров перенесены в 8020; здесь не рассылаем напрямую.

        return {"status": "sent", "message_id": sent.message_id}
    except Exception as e:
        error_trace = traceback.format_exc()
        logging.error(f"[process_hangup] An unexpected error occurred: {e}")
        logging.error(f"[process_hangup] Full traceback: {error_trace}")
        logging.error(f"[process_hangup] Data that caused error: {data}")
        return {"status": "error", "error": str(e)}
