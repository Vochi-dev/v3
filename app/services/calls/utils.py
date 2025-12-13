# app/services/calls/utils.py

import asyncio
import logging
import threading
from datetime import datetime
from collections import defaultdict
import re
import phonenumbers
from telegram.error import BadRequest

from app.services.events import save_telegram_message

# Lock для предотвращения race condition при обновлении phone_message_tracker
_phone_tracker_lock = threading.Lock()

# ───────── In-memory stores (ИНДИВИДУАЛЬНЫЕ ДЛЯ КАЖДОГО CHAT_ID) ─────────
# Структура: {chat_id: {uid: data}}
dial_cache_by_chat       = defaultdict(dict)
bridge_store_by_chat     = defaultdict(dict)
active_bridges_by_chat   = defaultdict(dict)

# для историй: {chat_id: {phone: [records]}}
call_pair_message_map_by_chat = defaultdict(dict)
hangup_message_map_by_chat    = defaultdict(lambda: defaultdict(list))

# ─────── Новая система группировки событий (ИНДИВИДУАЛЬНАЯ) ───────
# Структура: {chat_id: {phone: tracker_data}}
phone_message_tracker_by_chat = defaultdict(dict)

# Структура для отслеживания состояния звонков
call_state_tracker = defaultdict(dict)

# ─────── Кэш trunk по UniqueId и внешнему номеру ───────
# Используется для передачи trunk из dial в bridge
# Структура: {unique_id: trunk} и {external_phone: trunk}
trunk_cache_by_uid = {}
trunk_cache_by_phone = {}

# ─────── Set для отслеживания dial событий ───────
# Используется в start.py для определения "пустых" start
# Если dial пришёл быстро после start - start не отправляем
dial_received_uids = set()

# ─────── Timestamp последнего hangup для каждого (chat_id, enterprise_number) ───────
# Используется в bridge.py для оптимизации переотправки
# Bridge переотправляется только если был hangup за последние N секунд от ТОГО ЖЕ юнита
# Ключ: (chat_id, enterprise_number), Значение: timestamp
last_hangup_time_by_chat_enterprise = {}

# ─────── Mapping bridge по внутреннему номеру ───────
# Используется для удаления "зависших" bridge при hangup
# Ключ: (chat_id, enterprise_number, internal_number)
# Значение: {"uid": uid, "message_id": msg_id}
bridge_by_internal = {}

# ===== ОБРАТНАЯ СОВМЕСТИМОСТЬ =====
# Для старого кода, который ещё использует глобальные переменные
# Будем использовать значения для суперюзера 374573193
SUPERUSER_CHAT_ID = 374573193

# Создаем псевдонимы для обратной совместимости
dial_cache = dial_cache_by_chat[SUPERUSER_CHAT_ID]
bridge_store = bridge_store_by_chat[SUPERUSER_CHAT_ID]  
active_bridges = active_bridges_by_chat[SUPERUSER_CHAT_ID]
call_pair_message_map = call_pair_message_map_by_chat[SUPERUSER_CHAT_ID]
hangup_message_map = hangup_message_map_by_chat[SUPERUSER_CHAT_ID]
phone_message_tracker = phone_message_tracker_by_chat[SUPERUSER_CHAT_ID]

def get_phone_for_grouping(data: dict) -> str:
    """
    Определяет номер телефона для группировки событий.
    ИСПРАВЛЕНО: Улучшенная логика для bridge событий с учётом Exten
    """
    # Для bridge событий нужна особая логика
    if "BridgeUniqueid" in data:
        # Это bridge событие
        caller = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        exten = data.get("Exten", "")
        
        # Для обычного исходящего: caller=internal, connected=<unknown>, Exten=external
        # Используем Exten если это внешний номер (10+ цифр)
        if exten and len(exten) >= 10 and exten.isdigit():
            return exten
        
        # Определяем внешний номер для группировки
        if caller and not is_internal_number(caller):
            return caller
        elif connected and connected not in ["", "unknown", "<unknown>"] and not is_internal_number(connected):
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

def should_send_as_comment(phone: str, event_type: str, chat_id: int = None) -> tuple[bool, int]:
    """
    Определяет, нужно ли отправить событие как комментарий к предыдущему сообщению.
    ОБНОВЛЕНО: теперь индивидуально для каждого chat_id
    
    Возвращает:
        (should_comment, reply_to_message_id)
    """
    if not phone or phone == "Номер не определен":
        return False, None
    
    if chat_id is None:
        chat_id = SUPERUSER_CHAT_ID
        
    tracker = phone_message_tracker_by_chat[chat_id].get(phone)
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

def update_phone_tracker(phone: str, message_id: int, event_type: str, data: dict, chat_id: int = None):
    """
    Обновляет трекер сообщений для номера телефона.
    Переводит статус из 'pending' в 'sent'.
    ОБНОВЛЕНО: теперь индивидуально для каждого chat_id + защита от race condition
    """
    if not phone or phone == "Номер не определен":
        return
    
    if chat_id is None:
        chat_id = SUPERUSER_CHAT_ID
    
    # Используем lock для предотвращения race condition
    with _phone_tracker_lock:
        phone_message_tracker_by_chat[chat_id][phone] = {
            'message_id': message_id,
            'event_type': event_type,
            'timestamp': datetime.now().isoformat(),
            'unique_id': data.get('UniqueId', ''),
            'call_type': data.get('CallType', 0),
            'status': 'sent'  # Сообщение отправлено
        }
        
        # Ограничиваем размер кеша для этого chat_id
        tracker = phone_message_tracker_by_chat[chat_id]
        if len(tracker) > 1000:
            # Удаляем самые старые записи
            sorted_phones = sorted(
                tracker.items(),
                key=lambda x: x[1]['timestamp']
            )
            for phone_to_remove, _ in sorted_phones[:100]:
                del tracker[phone_to_remove]

def should_replace_previous_message(phone: str, event_type: str, chat_id: int = None) -> tuple[bool, int]:
    """
    Определяет, нужно ли заменить предыдущее сообщение (удалить + отправить новое).
    ОБНОВЛЕНО: теперь индивидуально для каждого chat_id + защита от race condition
    
    ВАЖНО: Эта функция также РЕЗЕРВИРУЕТ слот для нового сообщения,
    чтобы следующий вызов для того же phone знал о pending сообщении.
    
    Согласно Пояснению:
    - bridge события заменяют dial события
    - каждое следующее bridge событие заменяет предыдущее bridge
    
    Возвращает:
        (should_replace, message_id_to_delete)
    """
    if not phone or phone == "Номер не определен":
        return False, None
    
    if chat_id is None:
        chat_id = SUPERUSER_CHAT_ID
    
    # Используем lock для предотвращения race condition
    with _phone_tracker_lock:
        tracker = phone_message_tracker_by_chat[chat_id].get(phone)
        
        if not tracker:
            # Нет предыдущего сообщения - резервируем слот с pending статусом
            phone_message_tracker_by_chat[chat_id][phone] = {
                'message_id': None,  # Будет заполнено после отправки
                'event_type': event_type,
                'timestamp': datetime.now().isoformat(),
                'unique_id': '',
                'call_type': 0,
                'status': 'pending'  # Отмечаем что сообщение в процессе отправки
            }
            return False, None
        
        # Если предыдущее сообщение в статусе pending - значит другой dial уже в процессе
        if tracker.get('status') == 'pending':
            # Всё равно заменяем - возвращаем None для message_id, но резервируем слот
            phone_message_tracker_by_chat[chat_id][phone] = {
                'message_id': None,
                'event_type': event_type,
                'timestamp': datetime.now().isoformat(),
                'unique_id': '',
                'call_type': 0,
                'status': 'pending'
            }
            return False, None  # Нечего удалять, т.к. предыдущий ещё не отправлен
        
        last_event = tracker['event_type']
        message_to_delete = tracker['message_id']
        
        # Bridge заменяет dial или предыдущий bridge
        if event_type == 'bridge' and last_event in ['dial', 'bridge']:
            # Резервируем слот
            phone_message_tracker_by_chat[chat_id][phone] = {
                'message_id': None,
                'event_type': event_type,
                'timestamp': datetime.now().isoformat(),
                'unique_id': '',
                'call_type': 0,
                'status': 'pending'
            }
            return True, message_to_delete
        
        # Dial заменяет start или предыдущий dial (когда линия занята и переключается на другую)
        if event_type == 'dial' and last_event in ['start', 'dial']:
            # Резервируем слот
            phone_message_tracker_by_chat[chat_id][phone] = {
                'message_id': None,
                'event_type': event_type,
                'timestamp': datetime.now().isoformat(),
                'unique_id': '',
                'call_type': 0,
                'status': 'pending'
            }
            return True, message_to_delete
        
        return False, None

# ───────── Кэш trunk для передачи между событиями ─────────
def save_trunk_for_call(unique_id: str, external_phone: str, trunk: str):
    """
    Сохраняет trunk из dial события для использования в bridge.
    """
    if trunk and unique_id:
        trunk_cache_by_uid[unique_id] = trunk
        logging.debug(f"[save_trunk_for_call] Saved trunk '{trunk}' for uid '{unique_id}'")
    if trunk and external_phone:
        trunk_cache_by_phone[external_phone] = trunk
        logging.debug(f"[save_trunk_for_call] Saved trunk '{trunk}' for phone '{external_phone}'")
    
    # Очистка старых записей (держим не более 500)
    if len(trunk_cache_by_uid) > 500:
        # Удаляем первые 100 записей
        keys_to_remove = list(trunk_cache_by_uid.keys())[:100]
        for k in keys_to_remove:
            del trunk_cache_by_uid[k]
    if len(trunk_cache_by_phone) > 500:
        keys_to_remove = list(trunk_cache_by_phone.keys())[:100]
        for k in keys_to_remove:
            del trunk_cache_by_phone[k]

def get_trunk_for_call(unique_id: str = None, external_phone: str = None) -> str:
    """
    Получает trunk для звонка из кэша.
    Сначала ищет по unique_id, потом по external_phone.
    """
    if unique_id and unique_id in trunk_cache_by_uid:
        return trunk_cache_by_uid[unique_id]
    if external_phone and external_phone in trunk_cache_by_phone:
        return trunk_cache_by_phone[external_phone]
    return ""

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
        
        # Получаем код страны и национальный номер
        country_code = parsed.country_code
        national = str(parsed.national_number)
        
        # Форматируем по международному стандарту с префиксом в скобках
        if country_code == 375 and len(national) == 9:
            # Беларусь: +375 (29) 625-40-70
            return f"+375 ({national[:2]}) {national[2:5]}-{national[5:7]}-{national[7:]}"
        elif country_code == 7 and len(national) == 10:
            # Россия: +7 (495) 123-45-67
            return f"+7 ({national[:3]}) {national[3:6]}-{national[6:8]}-{national[8:]}"
        elif country_code == 1 and len(national) == 10:
            # США/Канада: +1 (555) 123-4567
            return f"+1 ({national[:3]}) {national[3:6]}-{national[6:]}"
        elif country_code == 380 and len(national) == 9:
            # Украина: +380 (67) 123-45-67
            return f"+380 ({national[:2]}) {national[2:5]}-{national[5:7]}-{national[7:]}"
        else:
            # Для других стран пытаемся применить общую логику
            international = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            # Заменяем первый пробел на ( и добавляем ) после кода оператора
            parts = international.split(' ', 2)
            if len(parts) >= 2:
                country_part = parts[0]  # +XX
                operator_part = parts[1]  # YYY
                rest = ' '.join(parts[2:]) if len(parts) > 2 else ''
                return f"{country_part} ({operator_part}) {rest}".strip()
            else:
                return international
                
    except Exception:
        return phone


# ───────── Обновление истории ─────────
def update_call_pair_message(caller, callee, message_id, is_internal=False, chat_id=None):
    """
    ОБНОВЛЕНО: теперь индивидуально для каждого chat_id
    """
    if chat_id is None:
        chat_id = SUPERUSER_CHAT_ID
        
    if is_internal:
        key = tuple(sorted([caller, callee]))
    else:
        key = (caller,)
    call_pair_message_map_by_chat[chat_id][key] = message_id
    return key

def update_hangup_message_map(caller, callee, message_id,
                              is_internal=False,
                              call_status=-1, call_type=-1,
                              extensions=None, chat_id=None):
    """
    ОБНОВЛЕНО: теперь индивидуально для каждого chat_id
    """
    if chat_id is None:
        chat_id = SUPERUSER_CHAT_ID
        
    rec = {
        'message_id': message_id,
        'caller':      caller,
        'callee':      callee,
        'timestamp':   datetime.now().isoformat(),
        'call_status': call_status,
        'call_type':   call_type,
        'extensions':  extensions or []
    }
    hangup_message_map_by_chat[chat_id][caller].append(rec)
    if is_internal:
        hangup_message_map_by_chat[chat_id][callee].append(rec)
    # оставляем не более 5
    hangup_message_map_by_chat[chat_id][caller] = hangup_message_map_by_chat[chat_id][caller][-5:]
    if is_internal:
        hangup_message_map_by_chat[chat_id][callee] = hangup_message_map_by_chat[chat_id][callee][-5:]


def get_relevant_hangup_message_id(caller, callee, is_internal=False, chat_id=None):
    """
    ОБНОВЛЕНО: теперь индивидуально для каждого chat_id
    """
    if chat_id is None:
        chat_id = SUPERUSER_CHAT_ID
        
    if is_internal:
        hist = hangup_message_map_by_chat[chat_id].get(caller, []) + hangup_message_map_by_chat[chat_id].get(callee, [])
    else:
        hist = hangup_message_map_by_chat[chat_id].get(caller, [])
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
