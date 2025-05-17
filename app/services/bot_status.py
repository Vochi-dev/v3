from aiogram import Bot
from aiogram.exceptions import TelegramError

async def check_bot_status(bot_token: str) -> bool:
    """
    Проверяет, доступен ли Telegram-бот с данным токеном.
    Возвращает True, если бот отвечает, иначе False.
    """
    try:
        bot = Bot(token=bot_token)
        await bot.get_me()
        await bot.session.close()
        return True
    except TelegramError:
        return False
    except Exception:
        return False
