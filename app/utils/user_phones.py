"""
Утилиты для работы с внутренними номерами пользователей
"""
import logging
from typing import Optional
from app.services.postgres import get_pool


async def get_all_internal_phones_by_tg_id(
    enterprise_number: str,
    telegram_tg_id: int
) -> list[str]:
    """
    Получить ВСЕ внутренние номера пользователя по telegram_tg_id
    
    Args:
        enterprise_number: Номер предприятия
        telegram_tg_id: Telegram ID пользователя
        
    Returns:
        Список внутренних номеров (отсортированный по возрастанию) или пустой список
    """
    try:
        pool = await get_pool()
        if not pool:
            logging.warning("[get_all_internal_phones_by_tg_id] Database pool not available")
            return []
            
        async with pool.acquire() as conn:
            query = """
                SELECT uip.phone_number
                FROM users u
                JOIN user_internal_phones uip ON u.id = uip.user_id
                WHERE u.enterprise_number = $1
                  AND u.telegram_tg_id = $2
                ORDER BY uip.phone_number ASC
            """
            results = await conn.fetch(query, enterprise_number, telegram_tg_id)
            
            if results:
                phones = [row['phone_number'] for row in results]
                logging.info(
                    f"[get_all_internal_phones_by_tg_id] Found {len(phones)} phone(s) {phones} "
                    f"for tg_id={telegram_tg_id}, enterprise={enterprise_number}"
                )
                return phones
            else:
                logging.info(
                    f"[get_all_internal_phones_by_tg_id] No internal phones found "
                    f"for tg_id={telegram_tg_id}, enterprise={enterprise_number}"
                )
                return []
                
    except Exception as e:
        logging.error(f"[get_all_internal_phones_by_tg_id] Error: {e}", exc_info=True)
        return []


async def get_min_internal_phone_by_tg_id(
    enterprise_number: str,
    telegram_tg_id: int
) -> Optional[str]:
    """
    Получить минимальный внутренний номер пользователя по telegram_tg_id
    
    Args:
        enterprise_number: Номер предприятия
        telegram_tg_id: Telegram ID пользователя
        
    Returns:
        Минимальный внутренний номер или None если не найден
    """
    phones = await get_all_internal_phones_by_tg_id(enterprise_number, telegram_tg_id)
    return phones[0] if phones else None


async def get_bot_owner_chat_id(asterisk_token: str) -> Optional[int]:
    """
    Получить chat_id владельца бота
    
    Args:
        asterisk_token: Токен Asterisk (name2 в enterprises, например "375293332255")
        
    Returns:
        chat_id владельца или None если не найден
    """
    try:
        pool = await get_pool()
        if not pool:
            logging.warning("[get_bot_owner_chat_id] Database pool not available")
            return None
            
        async with pool.acquire() as conn:
            query = """
                SELECT chat_id
                FROM enterprises
                WHERE name2 = $1
            """
            result = await conn.fetchrow(query, asterisk_token)
            
            if result and result['chat_id']:
                chat_id = int(result['chat_id'])
                logging.info(
                    f"[get_bot_owner_chat_id] Found owner chat_id={chat_id} "
                    f"for token={asterisk_token}"
                )
                return chat_id
            else:
                logging.info(
                    f"[get_bot_owner_chat_id] No owner chat_id found "
                    f"for token={asterisk_token}"
                )
                return None
                
    except Exception as e:
        logging.error(f"[get_bot_owner_chat_id] Error: {e}", exc_info=True)
        return None


async def get_enterprise_secret(asterisk_token: str) -> Optional[str]:
    """
    Получить secret предприятия для формирования ссылки на звонок
    
    Args:
        asterisk_token: Токен Asterisk (name2 в enterprises, например "375293332255")
        
    Returns:
        secret предприятия или None если не найден
    """
    try:
        pool = await get_pool()
        if not pool:
            logging.warning("[get_enterprise_secret] Database pool not available")
            return None
            
        async with pool.acquire() as conn:
            query = """
                SELECT secret
                FROM enterprises
                WHERE name2 = $1
            """
            result = await conn.fetchrow(query, asterisk_token)
            
            if result and result['secret']:
                secret = result['secret']
                logging.info(
                    f"[get_enterprise_secret] Found secret for token={asterisk_token}"
                )
                return secret
            else:
                logging.warning(
                    f"[get_enterprise_secret] No secret found for token={asterisk_token}"
                )
                return None
                
    except Exception as e:
        logging.error(f"[get_enterprise_secret] Error: {e}", exc_info=True)
        return None


def format_phone_with_click_to_call(
    phone_number: str,
    internal_phone: Optional[str],
    enterprise_secret: str,
    formatted_phone: str
) -> str:
    """
    Форматировать номер телефона с кликабельной ссылкой для инициации звонка
    
    Args:
        phone_number: Номер телефона в формате E.164 (например, "375296254070")
        internal_phone: Внутренний номер менеджера (например, "152") или None
        enterprise_secret: Secret предприятия из таблицы enterprises (например, "d68409d67e3b4b87a6675e76dae74a85")
        formatted_phone: Уже отформатированный номер для отображения (например, "+375 (29) 625-40-70")
        
    Returns:
        Строка с кликабельной ссылкой или просто отформатированный номер
        
    Example:
        >>> format_phone_with_click_to_call("375296254070", "152", "d68409d67e3b4b87a6675e76dae74a85", "+375 (29) 625-40-70")
        '<a href="https://bot.vochi.by/api/makecallexternal?code=152&phone=375296254070&clientId=d68409d67e3b4b87a6675e76dae74a85">+375 (29) 625-40-70</a>'
        
        >>> format_phone_with_click_to_call("375296254070", None, "d68409d67e3b4b87a6675e76dae74a85", "+375 (29) 625-40-70")
        '+375 (29) 625-40-70'
    """
    if not internal_phone:
        # Нет внутреннего номера - возвращаем просто текст
        return formatted_phone
    
    # Формируем URL для инициации звонка
    url = (
        f"https://bot.vochi.by/api/makecallexternal"
        f"?code={internal_phone}"
        f"&phone={phone_number}"
        f"&clientId={enterprise_secret}"
    )
    
    # Возвращаем HTML-ссылку для Telegram (поддерживает HTML разметку)
    return f'<a href="{url}">{formatted_phone}</a>'

