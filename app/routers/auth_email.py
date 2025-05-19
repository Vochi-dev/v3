# app/routers/auth_email.py
from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import aiosqlite

from app.services.email_verification import mark_verified
from app.config import settings
from aiogram import Bot as AiogramBot

router = APIRouter()


@router.get(
    "/verify-email/{token}", 
    response_class=HTMLResponse,
    summary="Подтверждение e-mail по path-параметру"
)
async def verify_email_path(token: str):
    """
    Подтверждение e-mail по URL-части /verify-email/{token}.
    Отмечает в базе и отправляет уведомление в чат.
    """
    return await _process_verification(token)


@router.get(
    "/verify-email", 
    response_class=HTMLResponse,
    summary="Подтверждение e-mail по query-параметру"
)
async def verify_email_query(request: Request):
    """
    Подтверждение e-mail по query-параметру /verify-email?token=...
    Отмечает в базе и отправляет уведомление в чат.
    """
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Параметр token обязателен"
        )
    return await _process_verification(token)


async def _process_verification(token: str) -> HTMLResponse:
    # отмечаем токен в БД
    ok, tg_id = await mark_verified(token)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Токен не найден или устарел"
        )

    # получаем bot_token из telegram_users
    bot_token = None
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM telegram_users WHERE tg_id = ?", (tg_id,)
        )
        row = await cur.fetchone()
        if row:
            bot_token = row["bot_token"]

    if not bot_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось определить бот для уведомления"
        )

    # отправляем пользователю в Telegram подтверждение
    bot = AiogramBot(token=bot_token)
    try:
        await bot.send_message(
            chat_id=tg_id,
            text="🎉 Почта подтверждена! Бот полностью готов к работе."
        )
    except Exception:
        # молча игнорируем неудачу отправки
        pass

    # возвращаем простую HTML-страницу
    return HTMLResponse(
        """
        <h1>Почта подтверждена!</h1>
        <p>Теперь вы можете вернуться в Telegram-бот и пользоваться всеми функциями.</p>
        """,
        status_code=200
    )
