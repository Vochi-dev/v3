#!/usr/bin/env python3
"""
Быстрый тест всех паттернов звонков для Telegram бота 0367
"""

import asyncio
import time
from telegram import Bot

def format_phone_display(phone: str) -> str:
    """Форматирует номер телефона для отображения по международному стандарту"""
    if not phone:
        return "Неизвестный"
    
    # Удаляем все не-цифровые символы
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) == 12 and digits.startswith('375'):
        # Белорусский номер: +375 (29) 123-45-67
        return f"+375 ({digits[3:5]}) {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif len(digits) == 11 and digits.startswith('7'):
        # Российский номер: +7 (999) 123-45-67
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    else:
        return phone

# Конфигурация для предприятия 0367  
BOT_TOKEN = "7280164925:AAHPPXH4Muq07RFMI_J5DyUhZXEo73l7LWI"
CHAT_ID = 374573193  # Правильный enterprise_chat_id из БД

async def test_call_pattern(bot: Bot, call_type: str, messages: list, delay: float = 2.0):
    """Тестирует эволюцию одного сообщения через несколько этапов"""
    call_id = f"test_{call_type}_{int(time.time())}"
    
    try:
        print(f"\n📱 Тестируем {call_type}...")
        
        # Отправляем первое сообщение
        message = await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🧪 ТЕСТ {call_type} - ЭТАП 1\n\n{messages[0]}",
            parse_mode="HTML"
        )
        message_id = message.message_id
        print(f"   ✅ Этап 1 отправлен (Message ID: {message_id})")
        
        # Редактируем сообщение через каждые delay секунд
        for i, msg in enumerate(messages[1:], 2):
            await asyncio.sleep(delay)
            
            await bot.edit_message_text(
                chat_id=CHAT_ID,
                message_id=message_id,
                text=f"🧪 ТЕСТ {call_type} - ЭТАП {i}\n\n{msg}",
                parse_mode="HTML"
            )
            print(f"   ✏️ Этап {i} отредактирован")
        
        print(f"   🎯 Тест {call_type} завершен! Финальный Message ID: {message_id}")
        return True
        
    except Exception as e:
        print(f"   ❌ Ошибка в тесте {call_type}: {e}")
        return False

async def run_all_tests():
    """Запускает все тесты по очереди"""
    bot = Bot(token=BOT_TOKEN)
    
    # Отправляем заголовок
    await bot.send_message(
        chat_id=CHAT_ID,
        text="🚀 <b>НАЧАЛО ТЕСТИРОВАНИЯ ВСЕХ ТИПОВ ЗВОНКОВ</b>\n\nКаждое сообщение будет эволюционировать через edit_message_text()",
        parse_mode="HTML"
    )
    
    tests = [
        ("1-1", [
            f"☎️152 ➡️ 💰{format_phone_display('375296123456')}\nЛиния: МТС-1",
            f"☎️152 📞➡️ 💰{format_phone_display('375296123456')}📞",
            f"✅ Успешный исходящий звонок\n💰{format_phone_display('375296123456')}\n☎️152\n⌛ Длительность: 03:45\n👤 Иванов Петр Петрович\n🔉Запись разговора"
        ]),
        
        ("2-1", [
            f"💰{format_phone_display('375447034448')} ➡️ Приветствие\n📡МТС Главный офис",
            f"💰{format_phone_display('375447034448')} ➡️ Доб.150,151,152\n📡МТС Главный офис\nЗвонил: 3 раза, Последний: 15.09.2025",
            f"☎️Иванов И.И. 📞➡️ 💰{format_phone_display('375447034448')}📞",
            f"✅ Успешный входящий звонок\n💰{format_phone_display('375447034448')}\n☎️Иванов И.И.\n📡МТС Главный офис\n⏰Начало звонка 14:25\n⌛ Длительность: 03:45\n👤 Петров Сергей Николаевич\n🔉Запись разговора"
        ]),
        
        ("2-18", [
            f"💰{format_phone_display('375447034448')} ➡️ Приветствие\n📡МТС Главный офис",
            f"💰{format_phone_display('375447034448')} ➡️ Доб.150,151,152\n📡МТС Главный офис",
            f"☎️Иванов И.И. 📞➡️ 💰{format_phone_display('375447034448')}📞",
            f"☎️Петров П.П. 📞➡️ 💰{format_phone_display('375447034448')}📞 (переведен от Иванова)",
            f"☎️Сидоров С.С. 📞➡️ 💰{format_phone_display('375447034448')}📞 (переведен от Петрова)",
            f"✅ Успешный входящий звонок\n💰{format_phone_display('375447034448')}\n☎️Сидоров С.С. [3 перевода: Иванов→Петров→Сидоров]\n📡МТС Главный офис\n⏰Начало звонка 14:25\n⌛ Длительность: 08:30\n👤 Кузнецов Алексей Владимирович\n🔉Запись разговора"
        ]),
        
        ("2-23", [
            f"💰{format_phone_display('375447034448')} ➡️ Доб.150 [FollowMe]\n📡МТС Главный офис",
            f"✅ Успешный входящий звонок [FollowMe]\n💰{format_phone_display('375447034448')}\n📞Принят на: мобильный {format_phone_display('375296254070')}\n📡МТС Главный офис\n⏰Начало звонка 14:25\n⌛ Длительность: 04:15\n🔉Запись разговора"
        ]),
        
        ("3-1", [
            "☎️152 📞➡️ ☎️185📞",
            "✅ Успешный внутренний звонок\n☎️152➡️\n☎️185\n⌛ Длительность: 01:30"
        ]),
        
        ("1-9", [
            f"☎️151 ➡️ 💰{format_phone_display('375296254070')}\nЛиния: МТС-1 ⚠️ЗАНЯТА → резерв МТС-2",
            f"☎️151 📞➡️ 💰{format_phone_display('375296254070')}📞",
            f"✅ Успешный исходящий звонок\n💰{format_phone_display('375296254070')}\n☎️151\n📡 Линия: МТС-2 [резерв]\n⌛ Длительность: 03:20\n👤 Петров Андрей Сергеевич\n🔉Запись разговора"
        ]),
        
        ("1-10", [
            f"☎️151 ➡️ 💰{format_phone_display('375296254070')}\nЛиния: МТС-1 ⚠️ЗАНЯТА",
            f"❌ Исходящий звонок не удался\n💰{format_phone_display('375296254070')}\n☎️151\n📡 МТС-1 занята, резерв недоступен\n⌛ Длительность: 00:05"
        ]),
        
        ("1-11", [
            f"🚫 Направление запрещено\n💰{format_phone_display('74957776644')}\n☎️151\n❌ Разрешены только звонки на +375\n⌛ Длительность: 00:01"
        ])
    ]
    
    success_count = 0
    total_tests = len(tests)
    
    for call_type, messages in tests:
        success = await test_call_pattern(bot, call_type, messages, delay=2.5)
        if success:
            success_count += 1
        
        # Пауза между тестами
        await asyncio.sleep(3)
    
    # Отправляем итоги
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"📊 <b>ИТОГИ ТЕСТИРОВАНИЯ</b>\n\nУспешно: {success_count}/{total_tests}\n\n{'✅ Все тесты пройдены!' if success_count == total_tests else '⚠️ Есть ошибки в тестах'}",
        parse_mode="HTML"
    )
    
    print(f"\n📊 ИТОГИ: {success_count}/{total_tests} тестов успешно")

if __name__ == "__main__":
    print("🧪 Запуск тестирования всех паттернов звонков...")
    asyncio.run(run_all_tests())
    print("🏁 Тестирование завершено!")
