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
from app.services.customers import upsert_customer_from_hangup
from app.services.postgres import get_pool
from app.services.asterisk_logs import save_asterisk_log
from app.services.postgres import get_pool
from app.services.metadata_client import metadata_client, extract_internal_phone_from_channel, extract_line_id_from_exten
from app.utils.logger_client import call_logger
from app.utils.user_phones import (
    get_all_internal_phones_by_tg_id,
    get_bot_owner_chat_id,
    get_enterprise_secret
)

def get_recording_link_text(call_record_info):
    """
    Формирует кликабельную ссылку на запись разговора для Telegram
    """
    if call_record_info and call_record_info.get('call_url'):
        call_url = call_record_info['call_url']
        return f'\n🔉<a href="{call_url}">Запись разговора</a>'
    else:
        # Если ссылка недоступна, показываем обычный текст
        return f'\n🔉Запись разговора'
from .utils import (
    format_phone_number,
    get_relevant_hangup_message_id,
    update_call_pair_message,
    update_hangup_message_map,
    dial_cache,
    bridge_store,
    active_bridges,
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
        # Сохраняем лог в asterisk_logs
        await save_asterisk_log(data)

        # Получаем номер для группировки событий
        phone_for_grouping = get_phone_for_grouping(data)

        # ───────── Шаг 1. Извлечение данных ─────────
        uid = data.get("UniqueId", "")
        caller = data.get("CallerIDNum", "") or ""
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

        # ───────── Логирование hangup события в Call Logger (ФОНОВО) ─────────
        try:
            await call_logger.log_call_event(
                enterprise_number=enterprise_number,
                unique_id=uid,
                event_type="hangup",
                event_data=data,
                chat_id=chat_id,
                background=True
            )
            logging.info(f"[process_hangup] Queued hangup event to Call Logger: {uid}")
        except Exception as e:
            logging.warning(f"[process_hangup] Failed to queue hangup event: {e}")

        # БЕЗОПАСНАЯ ПРОВЕРКА МАССИВОВ
        try:
            if exts and len(exts) > 0:
                logging.info(f"[process_hangup] DEBUG: exts[0] = '{exts[0]}'")
            else:
                logging.info(f"[process_hangup] DEBUG: exts is empty or None")
        except Exception as e:
            logging.error(f"[process_hangup] ERROR accessing exts: {e}, exts={exts}")
            exts = []  # Обнуляем если есть проблемы

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
        try:
            start_time_str = data.get("StartTime", "")
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
        
        # Для внутренних звонков
        if call_type == 2 or (caller_is_internal and connected and is_internal_number(connected)):
            call_direction = "internal"
            callee = connected or (exts[0] if exts and len(exts) > 0 else "")
        else:
            # Внешние звонки
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
        
        # ───────── ВКЛЮЧАЕМ обогащение метаданными для hangup ─────────
        enriched_data = {}
        if call_direction in ["incoming", "outgoing"] and enterprise_number != "0000":
            try:
                # ОТЛАДКА: Логируем параметры обогащения
                logging.info(f"[process_hangup] Enrichment params: enterprise_number={enterprise_number}, internal_phone={internal_phone}, external_phone={external_phone}, line_id={line_id}")
                
                enriched_data = await metadata_client.enrich_message_data(
                    enterprise_number=enterprise_number,
                    internal_phone=internal_phone if internal_phone else None,
                    line_id=line_id if line_id else None,
                    external_phone=external_phone if external_phone else None
                )
                logging.info(f"[process_hangup] Enriched data: {enriched_data}")
            except Exception as e:
                logging.error(f"[process_hangup] Error enriching metadata: {e}")
        
        # ───────── Шаг 6. Формируем текст согласно Пояснению ─────────
        
        if call_direction == "internal":
            # Внутренние звонки
            if call_status == 2:
                # Успешный внутренний звонок
                # Обогащаем ФИО для внутренних номеров
                caller_display = caller
                connected_display = connected
                
                # ФИО для внутренних номеров отключено для устранения блокировок
                
                text = (f"✅Успешный внутренний звонок\n"
                       f"☎️{caller_display}➡️\n"
                       f"☎️{connected_display}")
                if duration_text:
                    # ИСПРАВЛЕНО: Используем безопасный парсинг StartTime
                    start_time = data.get('StartTime', '')
                    if start_time:
                        try:
                            if 'T' in start_time:
                                time_part = start_time.split('T')[1][:5]
                            elif ' ' in start_time:
                                parts = start_time.split(' ')
                                if len(parts) >= 2:
                                    time_part = parts[1][:5]
                                else:
                                    time_part = "неизв"
                            else:
                                time_part = "неизв"
                            text += f"\n⏰Начало звонка {time_part}"
                        except Exception as e:
                            logging.warning(f"[process_hangup] Error parsing StartTime '{start_time}': {e}")
                            text += f"\n⏰Начало звонка неизв"
                    text += f"\n⌛ Длительность: {duration_text}"
                    text += get_recording_link_text(call_record_info)
            else:
                # Неуспешный внутренний звонок
                # Используем те же обогащённые отображения
                text = (f"❌ Коллега не поднял трубку\n"
                       f"☎️{caller_display}➡️\n" 
                       f"☎️{connected_display}")
                if duration_text:
                    # ИСПРАВЛЕНО: Используем безопасный парсинг StartTime
                    start_time = data.get('StartTime', '')
                    if start_time:
                        try:
                            if 'T' in start_time:
                                time_part = start_time.split('T')[1][:5]
                            elif ' ' in start_time:
                                parts = start_time.split(' ')
                                if len(parts) >= 2:
                                    time_part = parts[1][:5]
                                else:
                                    time_part = "неизв"
                            else:
                                time_part = "неизв"
                            text += f"\n⏰Начало звонка {time_part}"
                        except Exception as e:
                            logging.warning(f"[process_hangup] Error parsing StartTime '{start_time}': {e}")
                            text += f"\n⏰Начало звонка неизв"
                    text += f"\n⌛ Длительность: {duration_text}"
        
        elif call_direction == "incoming":
            # Входящие звонки
            phone = format_phone_number(caller)
            display = phone if not phone.startswith("+000") else "Номер не определен"
            
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
                
                # Добавляем всех, кому звонили (с обогащением ФИО)
                if exts:
                    internal_exts = [ext for ext in exts if is_internal_number(ext)]
                    mobile_exts = [ext for ext in exts if not is_internal_number(ext)]
                    
                    for ext in internal_exts:
                        # ФИО для внутренних номеров отключено для устранения блокировок
                        text += f"\n☎️{ext}"
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
            
            # Если не нашли внешний номер, используем данные из события
            if not external_phone:
                external_phone = data.get("Phone", "") or data.get("ConnectedLineNum", "") or ""
                
            phone = format_phone_number(external_phone)
            display = phone if not phone.startswith("+000") else "Номер не определен"
            
            # Получаем ВСЕ внутренние номера для текущего chat_id
            user_internal_phones = []
            owner_chat_id = None
            enterprise_secret = None
            clean_phone = None
            
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
        if user_internal_phones and enterprise_secret and clean_phone:
            # python-telegram-bot синтаксис (не aiogram!)
            buttons = []
            for internal_phone in user_internal_phones:
                button = InlineKeyboardButton(
                    text=f"📞 Позвонить с {internal_phone}",
                    callback_data=f"call:{clean_phone}:{internal_phone}:{enterprise_secret}"
                )
                buttons.append([button])  # Каждая кнопка на отдельной строке
            
            keyboard = InlineKeyboardMarkup(buttons)
            reply_markup = keyboard
            logging.info(
                f"[process_hangup] Added {len(user_internal_phones)} callback button(s) "
                f"for internal_phones={user_internal_phones}"
            )
        
        try:
            if should_comment and reply_to_id:
                logging.info(f"[process_hangup] Sending as comment to message {reply_to_id}")
                sent = await bot.send_message(
                    chat_id,
                    safe_text,
                    reply_to_message_id=reply_to_id,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=reply_markup
                )
                logging.info(f"[process_hangup] ✅ HANGUP COMMENT SENT: message_id={sent.message_id}")
            else:
                logging.info(f"[process_hangup] Sending as standalone message")
                sent = await bot.send_message(
                    chat_id, 
                    safe_text, 
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=reply_markup
                )
                logging.info(f"[process_hangup] ✅ HANGUP MESSAGE SENT: message_id={sent.message_id}")
                
        except BadRequest as e:
            logging.error(f"[process_hangup] ❌ send_message failed: {e}. text={safe_text!r}")
            return {"status": "error", "error": str(e)}
        
        # ───────── Шаг 8. ПОСЛЕ отправки hangup - удаляем bridge сообщения ─────────
        # Удаляем bridge сообщения для этого звонка
        bridge_messages_to_delete = []
        
        # ИСПРАВЛЕНО: Используем правильные индивидуальные хранилища для chat_id
        from .utils import bridge_store_by_chat, phone_message_tracker_by_chat
        
        # 1. Проверяем bridge_store по UniqueId для конкретного chat_id
        chat_bridge_store = bridge_store_by_chat[chat_id]
        if uid in chat_bridge_store:
            bridge_msg = chat_bridge_store.pop(uid)
            bridge_messages_to_delete.append(bridge_msg)
            logging.info(f"[process_hangup] Found bridge message {bridge_msg} in bridge_store for uid {uid}")
        
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
        for bridge_msg_id in bridge_messages_to_delete:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=bridge_msg_id)
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
        
        # ───────── Логируем отправленное Telegram сообщение ─────────
        try:
            await call_logger.log_telegram_message(
                enterprise_number=enterprise_number,
                unique_id=uid,
                chat_id=chat_id,
                message_type="hangup",
                action="send",
                message_id=sent.message_id,
                message_text=safe_text
            )
        except Exception as e:
            logging.warning(f"[process_hangup] Failed to log telegram message: {e}")
        
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

        logging.info(f"[process_hangup] Successfully sent hangup message {sent.message_id} for {phone_for_grouping}")

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
