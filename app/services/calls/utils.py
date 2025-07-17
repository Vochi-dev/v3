# app/services/calls/utils.py

import asyncio
import logging
from datetime import datetime
from collections import defaultdict
import re
import phonenumbers
from telegram.error import BadRequest

from app.services.events import save_telegram_message

# ───────── In-memory stores ─────────
dial_cache       = {}
bridge_store     = {}
active_bridges   = {}

# для историй
call_pair_message_map = {}
hangup_message_map    = defaultdict(list)

# ─────── Новая система группировки событий (17.01.2025) ───────
# Ключ: номер телефона, значение: информация о последнем сообщении
phone_message_tracker = {}

# Структура для отслеживания состояния звонков
call_state_tracker = defaultdict(dict)

def get_phone_for_grouping(data: dict) -> str:
    """
    Определяет номер телефона для группировки событий.
    ИСПРАВЛЕНО: Улучшенная логика для bridge событий
    """
    # Для bridge событий нужна особая логика
    if "BridgeUniqueid" in data:
        # Это bridge событие
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        
        # Определяем внешний номер для группировки
        if caller and not is_internal_number(caller):
            return caller
        elif connected and not is_internal_number(connected):
            return connected
        else:
            # Если оба внутренние, берем первый
            return caller or connected or data.get("UniqueId", "")
    
    # Для остальных событий используем старую логику
    phone = data.get("Phone", "") or data.get("CallerIDNum", "")
    if not phone:
        exts = data.get("Extensions", [])
        if exts:
            # Для внутренних звонков берем первый не-внутренний номер
            for ext in exts:
                if not is_internal_number(ext):
                    phone = ext
                    break
            if not phone:
                phone = exts[0]  # Если все внутренние, берем первый
    return phone

def should_send_as_comment(phone: str, event_type: str) -> tuple[bool, int]:
    """
    Определяет, нужно ли отправить событие как комментарий к предыдущему сообщению.
    
    Возвращает:
        (should_comment, reply_to_message_id)
    """
    if not phone or phone == "Номер не определен":
        return False, None
        
    tracker = phone_message_tracker.get(phone)
    if not tracker:
        return False, None
    
    # Проверяем время последнего сообщения (не более 10 минут)
    last_time = datetime.fromisoformat(tracker['timestamp'])
    now = datetime.now()
    if (now - last_time).total_seconds() > 600:  # 10 минут
        return False, None
    
    # Логика комментирования согласно Пояснению:
    # - dial события комментируют start события
    # - bridge события заменяют dial события (НЕ комментируют)
    # - hangup события комментируют bridge или dial события
    
    last_event = tracker['event_type']
    
    if event_type == 'dial' and last_event == 'start':
        return True, tracker['message_id']
    elif event_type == 'bridge' and last_event in ['dial', 'start']:
        # Bridge заменяет, а не комментирует
        return False, None
    elif event_type == 'hangup' and last_event in ['bridge', 'dial']:
        return True, tracker['message_id']
    
    return False, None

def update_phone_tracker(phone: str, message_id: int, event_type: str, data: dict):
    """
    Обновляет трекер сообщений для номера телефона
    """
    if not phone or phone == "Номер не определен":
        return
        
    phone_message_tracker[phone] = {
        'message_id': message_id,
        'event_type': event_type,
        'timestamp': datetime.now().isoformat(),
        'unique_id': data.get('UniqueId', ''),
        'call_type': data.get('CallType', 0)
    }
    
    # Ограничиваем размер кеша
    if len(phone_message_tracker) > 1000:
        # Удаляем самые старые записи
        sorted_phones = sorted(
            phone_message_tracker.items(),
            key=lambda x: x[1]['timestamp']
        )
        for phone_to_remove, _ in sorted_phones[:100]:
            del phone_message_tracker[phone_to_remove]

def should_replace_previous_message(phone: str, event_type: str) -> tuple[bool, int]:
    """
    Определяет, нужно ли заменить предыдущее сообщение (удалить + отправить новое).
    
    Согласно Пояснению:
    - bridge события заменяют dial события
    - каждое следующее bridge событие заменяет предыдущее bridge
    
    Возвращает:
        (should_replace, message_id_to_delete)
    """
    if not phone or phone == "Номер не определен":
        return False, None
        
    tracker = phone_message_tracker.get(phone)
    if not tracker:
        return False, None
    
    last_event = tracker['event_type']
    
    # Bridge заменяет dial или предыдущий bridge
    if event_type == 'bridge' and last_event in ['dial', 'bridge']:
        return True, tracker['message_id']
    
    # Dial заменяет start (существующая логика)
    if event_type == 'dial' and last_event == 'start':
        return True, tracker['message_id']
    
    return False, None

# ───────── Утилиты для номеров ─────────
def is_internal_number(number: str) -> bool:
    return bool(number and re.fullmatch(r"\d{3,4}", number))

def format_phone_number(phone: str) -> str:
    if not phone:
        return phone
    if is_internal_number(phone):
        return phone
    if not phone.startswith("+"):
        phone = "+" + phone
    try:
        parsed = phonenumbers.parse(phone, None)
        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except Exception:
        return phone


# ───────── Обновление истории ─────────
def update_call_pair_message(caller, callee, message_id, is_internal=False):
    if is_internal:
        key = tuple(sorted([caller, callee]))
    else:
        key = (caller,)
    call_pair_message_map[key] = message_id
    return key

def update_hangup_message_map(caller, callee, message_id,
                              is_internal=False,
                              call_status=-1, call_type=-1,
                              extensions=None):
    rec = {
        'message_id': message_id,
        'caller':      caller,
        'callee':      callee,
        'timestamp':   datetime.now().isoformat(),
        'call_status': call_status,
        'call_type':   call_type,
        'extensions':  extensions or []
    }
    hangup_message_map[caller].append(rec)
    if is_internal:
        hangup_message_map[callee].append(rec)
    # оставляем не более 5
    hangup_message_map[caller]   = hangup_message_map[caller][-5:]
    if is_internal:
        hangup_message_map[callee] = hangup_message_map[callee][-5:]


def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    if is_internal:
        hist = hangup_message_map.get(caller, []) + hangup_message_map.get(callee, [])
    else:
        hist = hangup_message_map.get(caller, [])
    if not hist:
        return None
    hist.sort(key=lambda x: x['timestamp'], reverse=True)
    return hist[0]['message_id']


def get_last_call_info(external_number: str) -> str:
    """
    Возвращает информацию о последнем звонке в формате из Пояснения:
    "Звонил: 16 раз
    Последний раз: 04.07.2025 в 14:13  
    ✅ Разговаривал: ☎️165 Иванов Иван"
    """
    hist = hangup_message_map.get(external_number, [])
    if not hist:
        return ""
    
    # Сортируем по времени, последний звонок первым
    sorted_hist = sorted(hist, key=lambda x: x['timestamp'], reverse=True)
    last = sorted_hist[0]
    
    # Считаем общее количество звонков
    call_count = len(hist)
    
    # Форматируем время последнего звонка
    try:
        ts = datetime.fromisoformat(last['timestamp'])
        ts = ts.replace(hour=(ts.hour + 3) % 24)  # GMT+3
        when = ts.strftime("%d.%m.%Y в %H:%M")
    except:
        when = "недавно"
    
    # Определяем результат последнего звонка
    status = last.get('call_status', -1)
    ctype = last.get('call_type', -1)
    extensions = last.get('extensions', [])
    
    # Формируем базовую информацию
    result = f"Звонил: {call_count} раз"
    if call_count == 1:
        result = "Звонил: 1 раз"
    
    result += f"\nПоследний раз: {when}"
    
    # Добавляем результат звонка
    if status == 2:  # Успешный звонок
        result += "\n✅ Разговаривал"
        
        # Пытаемся найти оператора, который разговаривал
        if extensions:
            # Ищем внутренний номер среди extensions
            for ext in extensions:
                if ext and ext.isdigit() and len(ext) <= 4:
                    result += f": ☎️{ext}"
                    break
        
        # Если не нашли в extensions, используем callee
        elif last.get('callee') and last.get('callee').isdigit():
            result += f": ☎️{last.get('callee')}"
            
    else:  # Неуспешный звонок
        if ctype == 0:  # Входящий
            result += "\n❌ Мы не подняли трубку"
        elif ctype == 1:  # Исходящий  
            result += "\n❌ Клиент не поднял трубку"
        else:
            result += "\n❌ Не ответили"
    
    return result


async def create_resend_loop(dial_cache_arg, bridge_store_arg, active_bridges_arg,
                             bot, chat_id: int):
    """
    Переотправляет незакрытые bridge-сообщения каждые 10 сек.
    """
    while True:
        await asyncio.sleep(10)
        for uid, info in list(active_bridges_arg.items()):
            text    = info.get("text", "")
            cli     = info.get("cli")
            op      = info.get("op")
            is_int  = is_internal_number(cli) and is_internal_number(op)
            reply_id= get_relevant_hangup_message_id(cli, op, is_int)

            safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
            logging.debug(f"[resend_loop] => chat={chat_id}, text={safe_text!r}")

            try:
                if uid in bridge_store_arg:
                    await bot.delete_message(chat_id, bridge_store_arg[uid])
                if reply_id:
                    sent = await bot.send_message(
                        chat_id, safe_text,
                        reply_to_message_id=reply_id,
                        parse_mode="HTML"
                    )
                else:
                    sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
                bridge_store_arg[uid] = sent.message_id
                update_hangup_message_map(cli, op, sent.message_id, is_int)
                save_telegram_message(
                    sent.message_id, "bridge_resend",
                    info.get("token", ""),
                    cli, op, is_int
                )
            except BadRequest as e:
                logging.error(f"[resend_loop] failed for {uid}: {e}. text={safe_text!r}")
