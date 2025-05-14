# app/routers/auth_email.py
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import HTMLResponse
from app.services.email_verification import mark_verified
from telegram import Bot
from app.config import TELEGRAM_BOT_TOKEN

router = APIRouter()
notify_bot = Bot(token=TELEGRAM_BOT_TOKEN)

@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email(token: str):
    ok, tg_id = mark_verified(token)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Токен не найден или устарел")

    # шлём сообщение прямо в Telegram
    try:
        await notify_bot.send_message(chat_id=tg_id, text="🎉 Почта подтверждена! Бот полностью готов к работе.")
    except Exception:
        pass

    return """
    <h1>Почта подтверждена!</h1>
    <p>Теперь вы можете вернуться в Telegram-бот и пользоваться всеми функциями.</p>
    """
