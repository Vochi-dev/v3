import logging
from telegram import Bot
from telegram.error import BadRequest
import json
import hashlib
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
    Обрабатывает Asterisk-событие 'hangup':
    — удаляет все по UID,
    — рассчитывает длительность,
    — формирует итоговый текст,
    — отправляет (возможно reply_to),
    — обновляет историю и сохраняет в БД.
    """
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)

    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "") or ""
    exts      = data.get("Extensions", []) or []
    connected = data.get("ConnectedLineNum", "") or ""
    is_int    = bool(exts and exts[0].isdigit() and len(exts[0]) <= 4)
    callee    = exts[0] if exts else connected
    token     = data.get("Token", "")

    # Создаем запись в таблице calls
    if uid and token:
        await create_call_record(uid, token, data)

    # Чистим память
    bridge_store.pop(uid, None)
    dial_cache.pop(uid, None)
    active_bridges.pop(uid, None)

    # Рассчитываем duration
    dur = ""
    try:
        from datetime import datetime
        secs = int((
            datetime.fromisoformat(data.get("EndTime", "")) -
            datetime.fromisoformat(data.get("StartTime", ""))
        ).total_seconds())
        dur = f"{secs//60:02}:{secs%60:02}"
    except:
        pass

    # Формируем display-номер
    phone   = format_phone_number(caller)
    display = phone if not phone.startswith("+000") else "Номер не определен"
    cs = int(data.get("CallStatus", -1))
    ct = int(data.get("CallType", -1))

    # Итоговый текст
    if is_int:
        m = ("✅ Успешный внутренний звонок\n" if cs == 2 else "❌ Абонент не ответил\n")
        m += f"{caller} ➡️ {callee}\n⌛ {dur}"
    else:
        if ct == 1 and cs == 0:
            m = f"⬆️ ❌ Абонент не ответил\n💰 {display}"
        elif cs == 2:
            m = f"✅ Завершённый звонок\n💰 {display}\n⌛ {dur}"
        else:
            m = f"❌ Завершённый звонок\n💰 {display}\n⌛ {dur}"

    safe_text = m.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_hangup] => chat={chat_id}, text={safe_text!r}")

    # Временно отключаем reply_to из-за миграции SQLite->PostgreSQL
    # reply_id = get_relevant_hangup_message_id(caller, callee, is_int)
    
    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_hangup] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Обновляем историю
    update_call_pair_message(caller, callee, sent.message_id, is_int)
    update_hangup_message_map(caller, callee, sent.message_id, is_int, cs, ct, exts)

    # Сохраняем в БД
    await save_telegram_message(
        sent.message_id,
        "hangup",
        data.get("Token", ""),
        caller,
        callee,
        is_int
    )
    return {"status": "sent"}
