# app/services/calls/__init__.py

# Этот файл собирает все обработчики из подмодулей и перенаправляет их наружу

from .utils import is_internal_number, format_phone_number
from .start import process_start
from .dials import process_dial
from .bridge import process_bridge
from .hangup import process_hangup
# Для внутренних вызовов (между менеджерами)
from .internal import process_internal_start, process_internal_bridge, process_internal_hangup
from .utils import update_call_pair_message, update_hangup_message_map, get_relevant_hangup_message_id, get_last_call_info

# Loop переотправки мостов
from .bridge import create_resend_loop
