from app.services.postgres import (
    get_all_enterprises,
    get_enterprise_by_number,
    add_enterprise,
    update_enterprise,
    delete_enterprise,
    get_enterprises_with_tokens,
    get_enterprise_number_by_bot_token
)

# Экспортируем все функции из postgres.py
__all__ = [
    'get_all_enterprises',
    'get_enterprise_by_number',
    'add_enterprise',
    'update_enterprise',
    'delete_enterprise',
    'get_enterprises_with_tokens',
    'get_enterprise_number_by_bot_token'
]
