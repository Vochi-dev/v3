#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Auth Handler
Обработчик авторизации для Telegram-ботов предприятий
"""

import re
import logging
from typing import Optional
import httpx
from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logger = logging.getLogger(__name__)

# URL нашего telegram_auth_service
TELEGRAM_AUTH_SERVICE_URL = "http://localhost:8016"

class TelegramAuth(StatesGroup):
    """Состояния авторизации в Telegram"""
    waiting_email = State()
    waiting_code = State()

def create_auth_router() -> Router:
    """Создает Router с обработчиками авторизации"""
    router = Router(name="telegram_auth")

    @router.message(CommandStart())
    async def cmd_start(message: types.Message, state: FSMContext) -> None:
        """
        Обработчик команды /start
        Поддерживает авторизацию в формате: /start auth_USER_ID_ENTERPRISE_NUMBER
        """
        user_id = message.from_user.id
        username = message.from_user.username or "Пользователь"
        
        # Проверяем, это авторизация или обычный старт
        args = message.text.split()
        if len(args) > 1 and args[1].startswith("auth_"):
            # Это запрос авторизации
            auth_param = args[1]  # auth_USER_ID_ENTERPRISE_NUMBER
            
            # Парсим параметры
            match = re.match(r"auth_(\d*)_(\w+)", auth_param)
            if match:
                web_user_id, enterprise_number = match.groups()
                
                # Сохраняем данные в state
                await state.update_data(
                    web_user_id=web_user_id,
                    enterprise_number=enterprise_number
                )
                
                await message.answer(
                    f"🔐 Добро пожаловать в процедуру авторизации!\n\n"
                    f"Для авторизации в системе предприятия {enterprise_number} "
                    f"введите ваш корпоративный email:"
                )
                await state.set_state(TelegramAuth.waiting_email)
                
            else:
                await message.answer(
                    "❌ Неверный формат ссылки авторизации.\n"
                    "Пожалуйста, используйте ссылку из рабочего стола."
                )
        else:
            # Обычный /start
            bot_token = message.bot.token
            enterprise_number = await get_enterprise_by_bot_token(bot_token)
            enterprise_name = await get_enterprise_name(enterprise_number) if enterprise_number else "Неизвестно"
            
            # Проверяем, авторизован ли уже пользователь
            is_authorized = await check_user_authorization(user_id)
            
            if is_authorized:
                await message.answer(
                    f"✅ Добро пожаловать в Telegram-бот предприятия **{enterprise_name}**!\n\n"
                    f"Вы уже авторизованы в системе.\n"
                    f"Здесь вы будете получать уведомления о звонках и других событиях.\n\n"
                    f"📞 Техподдержка: @VochiSupport",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    f"👋 Здравствуйте, {username}!\n\n"
                    f"Это Telegram-бот предприятия **{enterprise_name}**.\n\n"
                    f"Для получения уведомлений о звонках вам необходимо авторизоваться.\n"
                    f"Пожалуйста, перейдите в рабочий стол системы и нажмите кнопку \"📱 Telegram-бот\".\n\n"
                    f"📞 Техподдержка: @VochiSupport",
                    parse_mode="Markdown"
                )

    @router.message(TelegramAuth.waiting_email)
    async def process_email(message: types.Message, state: FSMContext) -> None:
        """Обработка введенного email"""
        email = message.text.strip().lower()
        user_data = await state.get_data()
        
        # Базовая проверка email
        if "@" not in email or "." not in email:
            await message.answer("❌ Некорректный формат email. Попробуйте еще раз:")
            return
        
        try:
            # Отправляем запрос на начало авторизации
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{TELEGRAM_AUTH_SERVICE_URL}/start_auth_flow",
                    json={
                        "email": email,
                        "enterprise_number": user_data["enterprise_number"],
                        "telegram_id": message.from_user.id
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result["success"]:
                        await message.answer(
                            f"✅ {result['message']}\n\n"
                            f"Введите полученный 6-значный код:"
                        )
                        await state.update_data(email=email)
                        await state.set_state(TelegramAuth.waiting_code)
                    else:
                        await message.answer(f"❌ {result['message']}")
                        await state.clear()
                else:
                    await message.answer("❌ Ошибка сервера. Попробуйте позже.")
                    await state.clear()
                    
        except Exception as e:
            logger.error(f"Ошибка при запросе авторизации: {e}")
            await message.answer("❌ Ошибка подключения к серверу. Попробуйте позже.")
            await state.clear()

    @router.message(TelegramAuth.waiting_code)
    async def process_code(message: types.Message, state: FSMContext) -> None:
        """Обработка введенного кода"""
        code = message.text.strip()
        user_data = await state.get_data()
        
        # Проверка формата кода
        if not code.isdigit() or len(code) != 6:
            await message.answer("❌ Код должен состоять из 6 цифр. Попробуйте еще раз:")
            return
        
        try:
            # Отправляем запрос на верификацию кода
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{TELEGRAM_AUTH_SERVICE_URL}/verify_code",
                    json={
                        "email": user_data["email"],
                        "code": code,
                        "telegram_id": message.from_user.id
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result["success"]:
                        enterprise_name = await get_enterprise_name(user_data["enterprise_number"])
                        user_name = result["data"]["full_name"]
                        
                        await message.answer(
                            f"🎉 Авторизация успешна!\n\n"
                            f"Добро пожаловать, **{user_name}**!\n"
                            f"Вы успешно авторизованы в Telegram-боте предприятия **{enterprise_name}**.\n\n"
                            f"Теперь вы будете получать уведомления о:\n"
                            f"📞 Входящих и исходящих звонках\n"
                            f"📋 Важных событиях системы\n"
                            f"📊 Отчетах и статистике\n\n"
                            f"📞 Техподдержка: @VochiSupport",
                            parse_mode="Markdown"
                        )
                    else:
                        await message.answer(f"❌ {result['message']}")
                else:
                    await message.answer("❌ Ошибка сервера. Попробуйте позже.")
                    
            await state.clear()
                    
        except Exception as e:
            logger.error(f"Ошибка при верификации кода: {e}")
            await message.answer("❌ Ошибка подключения к серверу. Попробуйте позже.")
            await state.clear()

    return router

# ═══════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

async def get_enterprise_by_bot_token(bot_token: str) -> Optional[str]:
    """Получить номер предприятия по токену бота"""
    try:
        async with httpx.AsyncClient() as client:
            # Здесь можно добавить запрос к БД или использовать существующий API
            # Пока возвращаем None, так как нужно реализовать endpoint
            return None
    except:
        return None

async def get_enterprise_name(enterprise_number: str) -> str:
    """Получить название предприятия"""
    try:
        async with httpx.AsyncClient() as client:
            # Здесь можно добавить запрос к БД или использовать существующий API
            return enterprise_number  # Временно возвращаем номер
    except:
        return enterprise_number or "Неизвестно"

async def check_user_authorization(telegram_id: int) -> bool:
    """Проверить авторизован ли пользователь"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{TELEGRAM_AUTH_SERVICE_URL}/check_auth_status/{telegram_id}",
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("authorized", False)
    except:
        pass
    return False