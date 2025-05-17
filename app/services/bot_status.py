from telegram import Bot
from telegram.error import TelegramError  # <-- из python-telegram-bot

async def check_bot_status(bot_token: str) -> bool:
    try:
        bot = Bot(token=bot_token)
        await bot.get_me()
        await bot.session.close()
        return True
    except TelegramError:
        return False
    except Exception:
        return False
