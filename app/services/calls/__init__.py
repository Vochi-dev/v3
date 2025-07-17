# app/services/calls/__init__.py

from .utils import (
    # утилиты
    is_internal_number,
    format_phone_number,
    update_call_pair_message,
    update_hangup_message_map,
    get_relevant_hangup_message_id,
    get_last_call_info,
    create_resend_loop,
    # in-memory сторы
    dial_cache,
    bridge_store,
    active_bridges,
)

# основные обработчики
from .start import process_start
from .dial import process_dial
from .bridge import process_bridge
from .hangup import process_hangup

# новые обработчики для модернизации (17.01.2025)
from .bridge import (
    process_bridge_create,
    process_bridge_leave,
    process_bridge_destroy,
    process_new_callerid
)

# (если у вас есть ещё и внутренние звонки)
try:
    from .internal import (
        process_internal_start,
        process_internal_bridge,
        process_internal_hangup,
    )
except ImportError:
    # модуль internal ещё не готов — просто пропускаем
    pass
