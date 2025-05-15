# app/services/calls/utils.py

import re
import phonenumbers
from datetime import datetime
from collections import defaultdict

# ————— in-memory хранилища —————
# Для reply_to hangup-сообщений: номер → список последних записей
hangup_message_map = defaultdict(list)
# Для связи «пара звонок→message_id»: (caller[, callee]) → message_id
call_pair_message_map = {}


# ───────── Утилиты для номеров ─────────
def is_internal_number(number: str) -> bool:
    """Внутренний номер — 3–4 цифры."""
    return bool(number and re.fullmatch(r"\d{3,4}", number))


def format_phone_number(phone: str) -> str:
    """Красиво форматируем внешний номер через phonenumbers."""
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


# ───────── Обновление истории и reply_to ─────────
def update_call_pair_message(caller, callee, message_id, is_internal=False):
    """
    Сохраняем последнюю отправку для пары (caller, callee) или просто (caller,).
    """
    key = tuple(sorted([caller, callee])) if is_internal else (caller,)
    call_pair_message_map[key] = message_id
    return key


def update_hangup_message_map(caller, callee, message_id,
                              is_internal=False,
                              call_status=-1, call_type=-1,
                              extensions=None):
    """
    Сохраняем hangup-запись для reply_to.
    Для внешних — по caller; для внутренних — по обоим.
    Храним не более 5 последних записей.
    """
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

    hangup_message_map[caller] = hangup_message_map[caller][-5:]
    if is_internal:
        hangup_message_map[callee] = hangup_message_map[callee][-5:]


def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    """
    Возвращает message_id последнего hangup для reply_to.
    """
    hist = (hangup_message_map.get(caller, []) +
            hangup_message_map.get(callee, [])) if is_internal else \
           hangup_message_map.get(caller, [])
    if not hist:
        return None
    hist.sort(key=lambda x: x['timestamp'], reverse=True)
    return hist[0]['message_id']


def get_last_call_info(external_number: str) -> str:
    """
    Возвращает строку с датой и иконкой последнего внешнего звонка.
    """
    hist = hangup_message_map.get(external_number, [])
    if not hist:
        return ""
    last = sorted(hist, key=lambda x: x['timestamp'], reverse=True)[0]
    ts = datetime.fromisoformat(last['timestamp'])
    ts = ts.replace(hour=(ts.hour + 3) % 24)  # поправка GMT+3
    when = ts.strftime("%d.%m.%Y %H:%M")
    icon = "✅" if last['call_status'] == 2 else "❌"
    if last['call_type'] == 0:
        return f"🛎️ Последний: {when}\n{icon}"
    return f"⬆️ Последний: {when}\n{icon}"
