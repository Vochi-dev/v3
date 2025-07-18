import logging
from telegram import Bot
from telegram.error import BadRequest
import json
import hashlib
import traceback
from datetime import datetime

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from app.services.postgres import get_pool
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

async def create_call_record(unique_id: str, token: str, data: dict):
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
            
            # Рассчитываем duration
            duration = 0
            if start_time_str and end_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str)
                    end_time = datetime.fromisoformat(end_time_str)
                    duration = int((end_time - start_time).total_seconds())
                except:
                    pass
            
            # Создаем запись в calls
            insert_query = """
                INSERT INTO calls (
                    unique_id, token, enterprise_id, phone_number, 
                    call_status, call_type, duration, data_source, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (unique_id) DO NOTHING
                RETURNING id
            """
            
            result = await connection.fetchrow(
                insert_query,
                unique_id, hashed_token, enterprise_id, phone_number,
                call_status, call_type, duration, 'live', datetime.now()
            )
            
            if result:
                call_id = result['id']
                logging.info(f"Created call record id={call_id} for {unique_id}")
                
                # Помечаем событие как обработанное
                update_query = """
                    UPDATE call_events 
                    SET processed = true 
                    WHERE unique_id = $1 AND event_type = 'hangup'
                """
                await connection.execute(update_query, unique_id)
                
                return call_id
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

        # Создаем запись в таблице calls
        if uid and token:
            await create_call_record(uid, token, data)

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
                    text += f"\n🔉Запись разговора"
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
                    text += f"\n🔉Запись разговора"
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
                    text += f"\n🔉Запись разговора"
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

        # Экранируем html-спецсимволы
        safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
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
        
        logging.info(f"[process_hangup] Successfully sent hangup message {sent.message_id} for {phone_for_grouping}")
        return {"status": "sent", "message_id": sent.message_id}
    except Exception as e:
        error_trace = traceback.format_exc()
        logging.error(f"[process_hangup] An unexpected error occurred: {e}")
        logging.error(f"[process_hangup] Full traceback: {error_trace}")
        logging.error(f"[process_hangup] Data that caused error: {data}")
        return {"status": "error", "error": str(e)}
