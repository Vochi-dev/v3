import logging
import asyncio
import aiohttp
from telegram import Bot
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
        
        # ───────── Шаг 5. Формируем текст согласно Пояснению ─────────
        
        if call_direction == "internal":
            # Внутренние звонки
            if call_status == 2:
                # Успешный внутренний звонок
                text = (f"✅Успешный внутренний звонок\n"
                       f"☎️{caller}➡️\n"
                       f"☎️{connected}")
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
                text = (f"❌ Коллега не поднял трубку\n"
                       f"☎️{caller}➡️\n" 
                       f"☎️{connected}")
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
            
            if call_status == 2:
                # Успешный входящий звонок
                text = f"✅Успешный входящий звонок\n💰{display}"
                
                # Добавляем информацию об операторе
                if connected and is_internal_number(connected):
                    text += f"\n☎️{connected}"
                elif exts:
                    # Если есть расширения, берем последнее внутреннее
                    for ext in reversed(exts):
                        if is_internal_number(ext):
                            text += f"\n☎️{ext}"
                            break
                            
                # Добавляем линию
                if trunk_info:
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
                
                # Добавляем всех, кому звонили
                if exts:
                    internal_exts = [ext for ext in exts if is_internal_number(ext)]
                    mobile_exts = [ext for ext in exts if not is_internal_number(ext)]
                    
                    for ext in internal_exts:
                        text += f"\n☎️{ext}"
                    for ext in mobile_exts:
                        text += f"\n📱{format_phone_number(ext)}"
                
                # Добавляем линию
                if trunk_info:
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
            
            if call_status == 2:
                # Успешный исходящий звонок
                text = f"✅Успешный исходящий звонок"
                if internal_caller:
                    text += f"\n☎️{internal_caller}"
                text += f"\n💰{display}"
                
                if trunk_info:
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
                if internal_caller:
                    text += f"\n☎️{internal_caller}"
                text += f"\n💰{display}"
                
                if trunk_info:
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
        
        try:
            if should_comment and reply_to_id:
                logging.info(f"[process_hangup] Sending as comment to message {reply_to_id}")
                sent = await bot.send_message(
                    chat_id,
                    safe_text,
                    reply_to_message_id=reply_to_id,
                    parse_mode="HTML"
                )
                logging.info(f"[process_hangup] ✅ HANGUP COMMENT SENT: message_id={sent.message_id}")
            else:
                logging.info(f"[process_hangup] Sending as standalone message")
                sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
                logging.info(f"[process_hangup] ✅ HANGUP MESSAGE SENT: message_id={sent.message_id}")
                
        except BadRequest as e:
            logging.error(f"[process_hangup] ❌ send_message failed: {e}. text={safe_text!r}")
            return {"status": "error", "error": str(e)}
        
        # ───────── Шаг 8. ПОСЛЕ отправки hangup - удаляем bridge сообщения ─────────
        # Удаляем bridge сообщения для этого звонка
        bridge_messages_to_delete = []
        
        # 1. Проверяем bridge_store по UniqueId
        if uid in bridge_store:
            bridge_msg = bridge_store.pop(uid)
            bridge_messages_to_delete.append(bridge_msg)
        
        # 2. Проверяем phone_message_tracker на bridge сообщения
        # ИСПРАВЛЕНО: phone_message_tracker[phone] это один объект, не массив
        if phone_for_grouping in phone_message_tracker:
            tracker_data = phone_message_tracker[phone_for_grouping]
            # Проверяем что tracker_data это словарь
            if isinstance(tracker_data, dict) and tracker_data.get('event_type') == 'bridge':
                bridge_msg_id = tracker_data['message_id']
                bridge_messages_to_delete.append(bridge_msg_id)
                # Очищаем tracker
                del phone_message_tracker[phone_for_grouping]
        
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
        
        # ───────── Шаг 11. Уведомление U‑ON через 8020 (реальный звонок завершён) ─────────
        try:
            ext_for_notify = exts[0] if exts else (connected or "")
            notify_payload = {
                "enterprise_number": token,
                "phone": caller,
                "extension": ext_for_notify,
            }
            timeout2 = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout2) as session:
                await session.post("http://localhost:8020/notify/incoming", json=notify_payload)
        except Exception as e:
            logging.warning(f"[process_hangup] notify incoming failed: {e}")

        logging.info(f"[process_hangup] Successfully sent hangup message {sent.message_id} for {phone_for_grouping}")

        # ───────── Fire-and-forget обновление customers ─────────
        try:
            asyncio.create_task(upsert_customer_from_hangup(data))
        except Exception:
            pass

        # ───────── Fire-and-forget обогащение профиля и обновление customers, затем edit Telegram ─────────
        async def _enrich_and_edit():
            try:
                # 1) Определяем предприятие
                pool = await get_pool()
                if not pool:
                    return
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT number FROM enterprises WHERE name2 = $1 OR secret = $1 OR number = $1 LIMIT 1",
                        data.get("Token", "")
                    )
                    enterprise_number = row["number"] if row else None
                if not enterprise_number:
                    return

                # 2) Определяем внешний номер для профиля
                phone = data.get("Phone") or data.get("CallerIDNum") or data.get("ConnectedLineNum") or ""
                if not phone:
                    return

                # 3) Запрос профиля через 8020
                import httpx
                prof = None
                uon_source_raw = None
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        r = await client.get(f"http://127.0.0.1:8020/customer-profile/{enterprise_number}/{phone}")
                        if r.status_code == 200:
                            prof = r.json() or {}
                            # Если профиль получен через U-ON адаптер, 8020 может вернуть source.raw
                            try:
                                uon_source_raw = (prof.get("source") or {}).get("raw") if isinstance(prof, dict) else None
                            except Exception:
                                uon_source_raw = None
                except Exception:
                    prof = None

                if not isinstance(prof, dict):
                    return

                ln = (prof.get("last_name") or "").strip()
                fn = (prof.get("first_name") or "").strip()
                mn = (prof.get("middle_name") or "").strip()
                en = (prof.get("enterprise_name") or "").strip()

                if not (ln or fn or en):
                    return

                # 4) Обновляем таблицу customers, если поле пустое
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE customers
                        SET last_name = COALESCE($1, last_name),
                            first_name = COALESCE($2, first_name),
                            middle_name = COALESCE($3, middle_name),
                            enterprise_name = COALESCE($4, enterprise_name)
                        WHERE enterprise_number = $5 AND phone_e164 = $6
                        """,
                        ln or None, fn or None, mn or None, en or None,
                        enterprise_number, phone if phone.startswith("+") else "+" + ''.join(ch for ch in phone if ch.isdigit())
                    )

                # 4b) Связываем номер с person_uid при приоритетной U-ON, если смогли извлечь внешний ID
                try:
                    if uon_source_raw and isinstance(uon_source_raw, dict):
                        # пробуем достать customer/client id по распространённым ключам
                        for key in ("client_id", "id", "customer_id", "clientId"):
                            ext_id = uon_source_raw.get(key)
                            if isinstance(ext_id, (str, int)) and str(ext_id).strip():
                                try:
                                    from app.services.customers import merge_customer_identity
                                    await merge_customer_identity(
                                        enterprise_number=enterprise_number,
                                        phone_e164=phone if phone.startswith("+") else "+" + ''.join(ch for ch in phone if ch.isdigit()),
                                        source="uon",
                                        external_id=str(ext_id).strip(),
                                        fio={"last_name": ln, "first_name": fn, "middle_name": mn},
                                        set_primary=True,
                                    )
                                except Exception:
                                    pass
                                break
                except Exception:
                    pass

                # 4c) Если удалось получить person_uid из профиля — обновим ФИО по всем номерам этого клиента
                try:
                    person_uid = None
                    try:
                        person_uid = (prof.get("person_uid") or None) if isinstance(prof, dict) else None
                    except Exception:
                        person_uid = None
                    if person_uid and (ln or fn or mn):
                        from app.services.customers import update_fio_for_person
                        await update_fio_for_person(
                            enterprise_number=enterprise_number,
                            person_uid=str(person_uid),
                            fio={"last_name": ln, "first_name": fn, "middle_name": mn},
                            is_primary_source=True,
                        )
                except Exception:
                    pass

                # 5) Формируем подпись и edit Telegram сообщения
                parts = []
                if ln:
                    parts.append(ln)
                if fn:
                    parts.append(fn)
                full_name = " ".join(parts).strip()
                suffix = ""
                if full_name and en:
                    suffix = f"\n👤 {full_name} ({en})"
                elif full_name:
                    suffix = f"\n👤 {full_name}"
                elif en:
                    suffix = f"\n🏢 {en}"
                if not suffix:
                    return

                try:
                    # Edit последнего отправленного сообщения (sent.message_id уже есть в замыкании)
                    new_text = safe_text + suffix
                    await bot.edit_message_text(chat_id=chat_id, message_id=sent.message_id, text=new_text, parse_mode="HTML")
                except Exception:
                    pass
            except Exception:
                pass

        try:
            asyncio.create_task(_enrich_and_edit())
        except Exception:
            pass

        # ───────── Fire-and-forget отправка в Integration Gateway (8020) ─────────
        try:
            token_for_gateway = token
            unique_id_for_gateway = uid
            event_type_for_gateway = "hangup"
            record_url_for_gateway = (call_record_info or {}).get("call_url")

            async def _dispatch_to_gateway():
                try:
                    payload = {
                        "token": token_for_gateway,
                        "uniqueId": unique_id_for_gateway,
                        "event_type": event_type_for_gateway,
                        "raw": data,
                        "record_url": record_url_for_gateway,
                    }
                    timeout = aiohttp.ClientTimeout(total=2)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        logging.info(f"[process_hangup] gateway dispatch start: uid={unique_id_for_gateway} type={event_type_for_gateway}")
                        resp = await session.post(
                            "http://localhost:8020/dispatch/call-event",
                            json=payload,
                        )
                        try:
                            logging.info(f"[process_hangup] gateway dispatch done: uid={unique_id_for_gateway} status={resp.status}")
                        except Exception:
                            pass
                except Exception as e:
                    logging.warning(f"[process_hangup] gateway dispatch error: {e}")

            asyncio.create_task(_dispatch_to_gateway())
        except Exception as e:
            logging.warning(f"[process_hangup] failed to schedule gateway dispatch: {e}")

        return {"status": "sent", "message_id": sent.message_id}
    except Exception as e:
        error_trace = traceback.format_exc()
        logging.error(f"[process_hangup] An unexpected error occurred: {e}")
        logging.error(f"[process_hangup] Full traceback: {error_trace}")
        logging.error(f"[process_hangup] Data that caused error: {data}")
        return {"status": "error", "error": str(e)}
