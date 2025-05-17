from telegram import Bot
from telegram.error import TelegramError

async def check_bot_status(bot_token: str) -> bool:
    """
    Проверяет, доступен ли Telegram-бот с данным токеном.
    Возвращает True, если бот отвечает, иначе False.
    """
    try:
        bot = Bot(token=bot_token)
        await bot.get_me()
        return True
    except TelegramError:
        return False
    except Exception:
        return False
