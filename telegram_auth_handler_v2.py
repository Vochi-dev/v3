#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Auth Handler для aiogram 2.x
Обработчик авторизации для Telegram-ботов предприятий
"""

import re
import logging
from typing import Optional
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.filters import Text, Command
import asyncpg

logger = logging.getLogger(__name__)

# URL нашего telegram_auth_service
TELEGRAM_AUTH_SERVICE_URL = "http://localhost:8016"

class TelegramAuth(StatesGroup):
    """Состояния авторизации в Telegram"""
    waiting_email = State()
    waiting_code = State()

def register_auth_handlers(dp: Dispatcher, enterprise_number: str):
    """Регистрирует обработчики авторизации в dispatcher"""
    
    # Обработчик /start должен работать в ЛЮБОМ состоянии
    @dp.message_handler(Command('start'), state='*')
    async def cmd_start(message: types.Message, state: FSMContext):
        """
        Обработчик команды /start
        Всегда проверяет авторизацию и предлагает авторизоваться если нужно
        """
        # Сбрасываем любое текущее состояние, если оно есть
        current_state = await state.get_state()
        if current_state is not None:
            await state.finish()
            logger.info(f"Reset state {current_state} for user {message.from_user.id}")
        
        user_id = message.from_user.id
        username = message.from_user.username or "Пользователь"
        
        # Используем enterprise_number из внешней функции
        current_enterprise = enterprise_number
        logger.info(f"Enterprise number: {current_enterprise}")
        
        # Проверяем параметры ссылки (если есть)
        web_user_id = ""
        args = message.get_args()
        if args and args.startswith("auth_"):
            # Парсим параметры из ссылки
            match = re.match(r"auth_(\d*)_(\w+)", args)
            if match:
                web_user_id, link_enterprise = match.groups()
                # Используем enterprise из ссылки если указан, иначе текущий
                if link_enterprise:
                    current_enterprise = link_enterprise
        
        enterprise_name = await get_enterprise_name(current_enterprise) if current_enterprise else "june"
        
        # Проверяем, авторизован ли уже пользователь
        is_authorized = await check_user_authorization(user_id)
        
        if is_authorized:
            # Пользователь уже авторизован - приветствие с кнопкой Mini App
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
            
            # Создаем кнопку для открытия Mini App
            web_app_button = InlineKeyboardButton(
                text="🎯 Открыть CRM",
                web_app=WebAppInfo(url="https://bot.vochi.by/miniapp/")
            )
            keyboard = InlineKeyboardMarkup().add(web_app_button)
            
            await message.answer(
                f"✅ Добро пожаловать в Telegram-бот предприятия {enterprise_name}!\n\n"
                f"Вы уже авторизованы в системе.\n"
                f"Здесь вы будете получать уведомления о звонках и других событиях.\n\n"
                f"🎯 Используйте кнопку ниже для доступа к полному CRM интерфейсу:\n\n"
                f"📞 Техподдержка: @VochiSupport",
                reply_markup=keyboard
            )
            
            # Устанавливаем Menu Button для быстрого доступа к Mini App
            await setup_menu_button(
                bot=message.bot, 
                chat_id=message.chat.id, 
                enterprise_name=enterprise_name
            )
        else:
            # Пользователь НЕ авторизован - предлагаем авторизацию
            await message.answer(
                f"🔐 Добро пожаловать в Telegram-бот предприятия {enterprise_name}!\n\n"
                f"Для получения уведомлений о звонках необходимо авторизоваться.\n\n"
                f"👇 Введите ваш корпоративный email для начала авторизации:"
            )
            
            # Сохраняем данные в state
            await state.update_data(
                web_user_id=web_user_id,
                enterprise_number=current_enterprise
            )
            
            await TelegramAuth.waiting_email.set()

    @dp.message_handler(state=TelegramAuth.waiting_email)
    async def process_email(message: types.Message, state: FSMContext):
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
                        await TelegramAuth.waiting_code.set()
                    else:
                        await message.answer(f"❌ {result['message']}")
                        await state.finish()
                else:
                    await message.answer("❌ Ошибка сервера. Попробуйте позже.")
                    await state.finish()
                    
        except Exception as e:
            logger.error(f"Ошибка при запросе авторизации: {e}")
            await message.answer("❌ Ошибка подключения к серверу. Попробуйте позже.")
            await state.finish()

    @dp.message_handler(state=TelegramAuth.waiting_code)
    async def process_code(message: types.Message, state: FSMContext):
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
                        
                        # Создаем кнопку для открытия Mini App
                        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
                        
                        web_app_button = InlineKeyboardButton(
                            text="🎯 Открыть CRM",
                            web_app=WebAppInfo(url="https://bot.vochi.by/miniapp/")
                        )
                        keyboard = InlineKeyboardMarkup().add(web_app_button)
                        
                        await message.answer(
                            f"🎉 Авторизация успешна!\n\n"
                            f"Добро пожаловать, {user_name}!\n"
                            f"Вы успешно авторизованы в Telegram-боте предприятия {enterprise_name}.\n\n"
                            f"Теперь вы будете получать уведомления о:\n"
                            f"📞 Входящих и исходящих звонках\n"
                            f"📋 Важных событиях системы\n"
                            f"📊 Отчетах и статистике\n\n"
                            f"🎯 Используйте кнопку ниже для доступа к полному CRM интерфейсу:\n\n"
                            f"📞 Техподдержка: @VochiSupport",
                            reply_markup=keyboard
                        )
                        
                        # Устанавливаем Menu Button для быстрого доступа к Mini App
                        await setup_menu_button(
                            bot=message.bot, 
                            chat_id=message.chat.id, 
                            enterprise_name=enterprise_name
                        )
                    else:
                        await message.answer(f"❌ {result['message']}")
                else:
                    await message.answer("❌ Ошибка сервера. Попробуйте позже.")
                    
            await state.finish()
                    
        except Exception as e:
            logger.error(f"Ошибка при верификации кода: {e}")
            await message.answer("❌ Ошибка подключения к серверу. Попробуйте позже.")
            await state.finish()

# ═══════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

async def get_enterprise_by_bot_token(bot_token: str) -> Optional[str]:
    """Получить номер предприятия по токену бота"""
    try:
        # Прямой запрос к базе данных для получения enterprise_number по bot_token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TELEGRAM_AUTH_SERVICE_URL}/get_enterprise_by_token",
                json={"bot_token": bot_token},
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("enterprise_number")
    except Exception as e:
        logger.error(f"Ошибка получения enterprise_number: {e}")
    return None

async def get_enterprise_name(enterprise_number: str) -> str:
    """Получить название предприятия из БД"""
    try:
        import asyncpg
        
        # Подключение к базе данных
        DB_CONFIG = {
            'host': 'localhost',
            'port': 5432,
            'user': 'postgres', 
            'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
            'database': 'postgres'
        }
        
        conn = await asyncpg.connect(**DB_CONFIG)
        try:
            result = await conn.fetchrow(
                "SELECT name FROM enterprises WHERE number = $1",
                enterprise_number
            )
            
            if result and result['name']:
                return result['name']
            else:
                return enterprise_number  # fallback на номер если нет названия
                
        finally:
            await conn.close()
            
    except Exception as e:
        logger.error(f"Ошибка получения названия предприятия: {e}")
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

async def setup_menu_button(bot: Bot, chat_id: int = None, enterprise_name: str = "CRM"):
    """Установить постоянную Menu Button для доступа к Mini App"""
    try:
        # Подготавливаем данные для Menu Button
        menu_button_data = {
            "type": "web_app",
            "text": f"🎯 {enterprise_name}",
            "web_app": {
                "url": "https://bot.vochi.by/miniapp/"
            }
        }
        
        # Формируем URL для API
        if chat_id:
            # Для конкретного пользователя
            api_url = f"https://api.telegram.org/bot{bot._token}/setChatMenuButton"
            params = {
                "chat_id": chat_id,
                "menu_button": menu_button_data
            }
        else:
            # Для всех пользователей (глобально)
            api_url = f"https://api.telegram.org/bot{bot._token}/setChatMenuButton"
            params = {
                "menu_button": menu_button_data
            }
        
        # Отправляем запрос к Telegram API
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, json=params, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    logger.info(f"Menu Button установлена успешно для chat_id: {chat_id or 'все пользователи'}")
                    return True
                else:
                    logger.warning(f"Ошибка установки Menu Button: {result.get('description')}")
            else:
                logger.error(f"HTTP ошибка при установке Menu Button: {response.status_code}")
                
    except Exception as e:
        logger.error(f"Исключение при установке Menu Button: {e}")
    
    return False