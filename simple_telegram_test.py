#!/usr/bin/env python3
"""
Простой тест отправки сообщения в Telegram для проверки
"""

import asyncio
from telegram import Bot

# Конфигурация для предприятия 0367  
BOT_TOKEN = "7280164925:AAHPPXH4Muq07RFMI_J5DyUhZXEo73l7LWI"
CHAT_ID = 7055556176

async def test_simple_message():
    """Простая проверка отправки сообщения"""
    bot = Bot(token=BOT_TOKEN)
    
    try:
        # Отправляем тестовое сообщение
        message = await bot.send_message(
            chat_id=CHAT_ID,
            text="🧪 Тест: простое сообщение для проверки бота 0367",
            parse_mode="HTML"
        )
        
        print(f"✅ Сообщение отправлено! Message ID: {message.message_id}")
        
        # Ждем 3 секунды и редактируем
        await asyncio.sleep(3)
        
        await bot.edit_message_text(
            chat_id=CHAT_ID,
            message_id=message.message_id,
            text="🧪 Тест: сообщение отредактировано через edit_message_text!",
            parse_mode="HTML"
        )
        
        print(f"✅ Сообщение отредактировано! Message ID: {message.message_id}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_simple_message())
    if result:
        print("🎯 Telegram бот 0367 работает корректно!")
    else:
        print("💥 Проблема с Telegram ботом 0367!")
