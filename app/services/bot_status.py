from telegram.ext import ApplicationBuilder
from telegram.error import TelegramError

async def check_bot_status(bot_token: str) -> bool:
    try:
        app = ApplicationBuilder().token(bot_token).build()
        me = await app.bot.get_me()
        return True
    except TelegramError:
        return False
    except Exception:
        return False
