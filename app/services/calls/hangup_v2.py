import logging
import json
import hashlib
from datetime import datetime, timedelta
from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.services.asterisk_logs import save_asterisk_log
from app.services.postgres import get_pool
from .utils import format_phone_number, delete_previous_messages, escape_html

async def get_phone_statistics(phone_number: str, enterprise_id: str) -> dict:
    """
    Получает статистику звонков для номера из таблицы calls
    """
    pool = await get_pool()
    if not pool:
        return {"total_calls": 0, "successful_calls": 0, "last_call": "", "first_call": ""}
    
    try:
        async with pool.acquire() as conn:
            query = """
                SELECT 
                    COUNT(*) as total_calls,
                    COUNT(CASE WHEN call_status = '2' THEN 1 END) as successful_calls,
                    MAX(timestamp) as last_call_time,
                    MIN(timestamp) as first_call_time
                FROM calls 
                WHERE phone_number = $1 AND enterprise_id = $2
                GROUP BY phone_number
            """
            result = await conn.fetchrow(query, phone_number, enterprise_id)
            
            if result:
                last_call = result['last_call_time'].strftime("%d.%m.%Y в %H:%M") if result['last_call_time'] else ""
                first_call = result['first_call_time'].strftime("%d.%m.%Y") if result['first_call_time'] else ""
                
                return {
                    'total_calls': result['total_calls'],
                    'successful_calls': result['successful_calls'],
                    'last_call': last_call,
                    'first_call': first_call
                }
            else:
                return {"total_calls": 1, "successful_calls": 0, "last_call": "", "first_call": ""}
                
    except Exception as e:
        logging.error(f"Error getting phone statistics for {phone_number}: {e}")
        return {"total_calls": 0, "successful_calls": 0, "last_call": "", "first_call": ""}

async def get_line_info(trunk: str, enterprise_id: str) -> dict:
    """
    Получает информацию о линии из таблицы gsm_lines
    """
    pool = await get_pool()
    if not pool:
        return {"line_name": trunk, "phone_number": ""}
    
    try:
        async with pool.acquire() as conn:
            query = """
                SELECT line_name, phone_number 
                FROM gsm_lines 
                WHERE line_id = $1 AND enterprise_number = $2
                LIMIT 1
            """
            result = await conn.fetchrow(query, trunk, enterprise_id)
            
            if result and (result['line_name'] or result['phone_number']):
                return {
                    'line_name': result['line_name'] or trunk,
                    'phone_number': result['phone_number'] or ""
                }
            else:
                return {"line_name": trunk, "phone_number": ""}
                
    except Exception as e:
        logging.error(f"Error getting line info for {trunk}: {e}")
        return {"line_name": trunk, "phone_number": ""}

async def get_call_events(unique_id: str) -> list:
    """
    Получает все события звонка для анализа
    """
    pool = await get_pool()
    if not pool:
        return []
    
    try:
        async with pool.acquire() as conn:
            query = """
                SELECT event_type, raw_data, event_timestamp
                FROM call_events 
                WHERE unique_id = $1
                ORDER BY event_timestamp ASC
            """
            rows = await conn.fetch(query, unique_id)
            
            events = []
            for row in rows:
                events.append({
                    'event_type': row['event_type'],
                    'data': json.loads(row['raw_data']),
                    'timestamp': row['event_timestamp']
                })
            
            return events
            
    except Exception as e:
        logging.error(f"Error getting call events for {unique_id}: {e}")
        return []

async def find_related_calls(unique_id: str, phone_number: str, enterprise_id: str, timestamp: datetime) -> list:
    """
    Находит связанные звонки в временном окне ±60 секунд
    """
    pool = await get_pool()
    if not pool:
        return [unique_id]
    
    try:
        async with pool.acquire() as conn:
            # Ищем звонки в временном окне ±60 секунд с тем же номером
            query = """
                SELECT unique_id, timestamp
                FROM calls 
                WHERE phone_number = $1 
                  AND enterprise_id = $2
                  AND ABS(EXTRACT(EPOCH FROM (timestamp - $3))) <= 60
                ORDER BY timestamp
            """
            rows = await conn.fetch(query, phone_number, enterprise_id, timestamp)
            
            related_ids = [row['unique_id'] for row in rows]
            return related_ids if related_ids else [unique_id]
            
    except Exception as e:
        logging.error(f"Error finding related calls for {unique_id}: {e}")
        return [unique_id]

def format_message_template(call_data: dict, stats: dict, line_info: dict) -> str:
    """
    Формирует сообщение по шаблонам из Пояснение.txt
    """
    call_type = int(call_data.get('CallType', 0))
    call_status = int(call_data.get('CallStatus', 0))
    phone = escape_html(call_data.get('Phone', ''))
    extensions = call_data.get('Extensions', [])
    start_time = escape_html(call_data.get('StartTime', ''))
    end_time = escape_html(call_data.get('EndTime', ''))
    trunk = escape_html(call_data.get('Trunk', ''))
    
    # Форматируем номер
    formatted_phone = format_phone_number(phone)
    
    # Рассчитываем длительность
    duration = ""
    if start_time and end_time:
        try:
            start = datetime.fromisoformat(start_time)
            end = datetime.fromisoformat(end_time)
            total_seconds = int((end - start).total_seconds())
            duration = f"{total_seconds//60:02}:{total_seconds%60:02}"
        except:
            duration = "00:00"
    
    # Время начала звонка
    call_start_time = ""
    if start_time:
        try:
            start = datetime.fromisoformat(start_time)
            call_start_time = start.strftime("%H:%M")
        except:
            call_start_time = ""
    
    # Определяем линию
    line_display = line_info['line_name']
    if line_info['phone_number']:
        line_display = f"{line_info['phone_number']} {line_info['line_name']}"
    
    # Статистика звонков
    stats_text = ""
    if stats['total_calls'] > 1:
        stats_text = f"Звонил: {stats['total_calls']} раз\n"
        if stats['last_call']:
            stats_text += f"Последний раз: {stats['last_call']}\n"
    
    # ВНУТРЕННИЕ ЗВОНКИ (CallType = 2)
    if call_type == 2:
        caller = phone or extensions[0] if extensions else ""
        callee = extensions[0] if extensions else ""
        
        if call_status == 2:  # Успешный
            return f"""✅Успешный внутренний звонок
☎️{caller}➡️ ☎️{callee}
⏰Начало звонка {call_start_time}
⌛ Длительность: {duration}
🔉Запись разговора"""
        else:  # Неуспешный
            return f"""❌ Коллега не поднял трубку
☎️{caller}➡️ ☎️{callee}
⏰Начало звонка {call_start_time}
⌛ Дозванивались: {duration}"""
    
    # ВХОДЯЩИЕ ЗВОНКИ (CallType = 0)
    elif call_type == 0:
        if call_status == 2:  # Успешный входящий
            answered_by = ""
            if extensions:
                answered_by = f"☎️{extensions[0]}"
                # Здесь можно добавить поиск имени по номеру в БД
                
            return f"""✅Успешный входящий звонок
💰{formatted_phone}
{answered_by}
Линия: {line_display}
{stats_text}⏰Начало звонка {call_start_time}
⌛ Длительность: {duration}
🔉Запись разговора"""
        else:  # Неуспешный входящий
            ext_list = ""
            if extensions:
                ext_list = "\n".join([f"☎️{ext}" for ext in extensions])
                if len(extensions) > 1:
                    ext_list = "\n" + ext_list
            
            return f"""❌ Мы не подняли трубку
💰{formatted_phone}{ext_list}
Линия: {line_display}
{stats_text}⏰Начало звонка {call_start_time}
⌛ Дозванивались: {duration}"""
    
    # ИСХОДЯЩИЕ ЗВОНКИ (CallType = 1) 
    elif call_type == 1:
        caller_ext = extensions[0] if extensions else ""
        
        if call_status == 2:  # Успешный исходящий
            return f"""✅Успешный исходящий звонок
☎️{caller_ext}
💰{formatted_phone}
Линия: {line_display}
{stats_text}⏰Начало звонка {call_start_time}
⌛ Длительность: {duration}
🔉Запись разговора"""
        else:  # Неуспешный исходящий
            return f"""❌ Клиент не поднял трубку
☎️{caller_ext}
💰{formatted_phone}
Линия: {line_display}
{stats_text}⏰Начало звонка {call_start_time}
⌛ Дозванивались: {duration}"""
    
    # Fallback для неизвестных типов
    return f"""❌ Завершённый звонок
💰{formatted_phone}
⏰Начало звонка {call_start_time}
⌛ Длительность: {duration}"""

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
            phone_number = escape_html(data.get('Phone', data.get('CallerIDNum', '')))
            start_time_str = escape_html(data.get('StartTime', ''))
            end_time_str = escape_html(data.get('EndTime', ''))
            call_status = str(data.get('CallStatus', '0'))
            call_type = str(data.get('CallType', '0'))
            trunk = escape_html(data.get('Trunk', ''))
            
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
                    unique_id, token, enterprise_id, phone_number, trunk,
                    call_status, call_type, duration, start_time, end_time,
                    timestamp, data_source, raw_data, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                ON CONFLICT (unique_id) DO UPDATE SET
                    call_status = EXCLUDED.call_status,
                    end_time = EXCLUDED.end_time,
                    duration = EXCLUDED.duration,
                    raw_data = EXCLUDED.raw_data
                RETURNING id
            """
            
            timestamp = datetime.fromisoformat(start_time_str) if start_time_str else datetime.now()
            start_time_dt = datetime.fromisoformat(start_time_str) if start_time_str else None
            end_time_dt = datetime.fromisoformat(end_time_str) if end_time_str else None
            
            result = await connection.fetchrow(
                insert_query,
                unique_id, hashed_token, enterprise_id, phone_number, trunk,
                call_status, call_type, duration, start_time_dt, end_time_dt,
                timestamp, 'live', json.dumps(data), datetime.now()
            )
            
            if result:
                call_id = result['id']
                logging.info(f"Created/updated call record id={call_id} for {unique_id}")
                
                # Помечаем событие как обработанное
                update_query = """
                    UPDATE call_events 
                    SET processed = true 
                    WHERE unique_id = $1 AND event_type = 'hangup'
                """
                await connection.execute(update_query, unique_id)
                
                return call_id
                
    except Exception as e:
        logging.error(f"Error creating call record for {unique_id}: {e}")
        return None

async def process_hangup_v2(bot: Bot, chat_id: int, data: dict):
    """
    Новая версия обработчика hangup с использованием шаблонов из Пояснение.txt
    Удаляет предыдущие сообщения и отправляет финальное HANGUP сообщение.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)
    
    uid = data.get("UniqueId", "")
    token = data.get("Token", "")
    phone = escape_html(data.get("Phone", "") or data.get("CallerIDNum", ""))
    
    # Удаляем предыдущие сообщения для этого unique_id (START/DIAL/BRIDGE)
    deleted = await delete_previous_messages(bot, chat_id, uid)
    logging.info(f"[process_hangup_v2] Deleted {len(deleted)} previous messages for {uid}")
    
    if not uid or not token:
        logging.error(f"Missing required fields: UniqueId={uid}, Token={token}")
        return {"status": "error", "error": "Missing required fields"}
    
    # Создаем/обновляем запись в таблице calls
    # Используем общий UUID токен если он есть (для одинаковых ссылок во всех chat_id)
    shared_uuid = data.get("_shared_uuid_token", None)
    call_id = await create_call_record(uid, token, data, shared_uuid)
    
    # Получаем enterprise_id
    pool = await get_pool()
    enterprise_id = token[:4]  # fallback
    if pool:
        try:
            async with pool.acquire() as conn:
                enterprise_query = "SELECT number FROM enterprises WHERE name2 = $1 OR secret = $1 LIMIT 1"
                enterprise_result = await conn.fetchrow(enterprise_query, token)
                if enterprise_result:
                    enterprise_id = enterprise_result['number']
        except Exception as e:
            logging.error(f"Error getting enterprise_id: {e}")
    
    # Получаем статистику звонков
    stats = await get_phone_statistics(phone, enterprise_id)
    
    # Получаем информацию о линии
    trunk = escape_html(data.get('Trunk', ''))
    line_info = await get_line_info(trunk, enterprise_id)
    
    # Находим связанные звонки (для будущей группировки)
    timestamp = datetime.now()
    if data.get('StartTime'):
        try:
            timestamp = datetime.fromisoformat(data['StartTime'])
        except:
            pass
    
    related_calls = await find_related_calls(uid, phone, enterprise_id, timestamp)
    logging.info(f"Found {len(related_calls)} related calls for {uid}")
    
    # Формируем сообщение по шаблону
    message_text = format_message_template(data, stats, line_info)
    
    # Экранируем HTML
    safe_text = message_text.replace("<", "&lt;").replace(">", "&gt;")
    
    logging.info(f"[process_hangup_v2] => chat={chat_id}, text preview: {safe_text[:100]}...")
    
    # Отправляем в Telegram
    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
        
        # Определяем caller/callee из данных
        call_type_int = int(data.get("CallType", 0))
        extensions = data.get("Extensions", [])
        
        if call_type_int == 2:  # внутренний
            caller = phone or (extensions[0] if extensions else "")
            callee = extensions[1] if len(extensions) > 1 else ""
            is_internal = True
        elif call_type_int == 1:  # исходящий
            caller = extensions[0] if extensions else ""
            callee = phone
            is_internal = False
        else:  # входящий
            caller = phone
            callee = extensions[0] if extensions else ""
            is_internal = False
        
        # Сохраняем в обычном формате (HANGUP НЕ сохраняется в V2 памяти!)
        await save_telegram_message(
            message_id=sent.message_id,
            event_type="hangup_v2",
            token=token,
            caller=caller,
            callee=callee,
            is_internal=is_internal,
            call_status=int(data.get("CallStatus", 0)),
            call_type=call_type_int
        )
        
        logging.info(f"Successfully sent hangup_v2 message {sent.message_id} to chat {chat_id}")
        return {"status": "sent", "message_id": sent.message_id}
        
    except BadRequest as e:
        logging.error(f"[process_hangup_v2] send_message failed: {e}. text preview: {safe_text[:200]}...")
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logging.error(f"[process_hangup_v2] unexpected error: {e}")
        return {"status": "error", "error": str(e)} 