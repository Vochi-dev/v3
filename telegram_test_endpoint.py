#!/usr/bin/env python3
"""
Тестовый endpoint для проверки Telegram сообщений всех типов звонков
Для предприятия 0367 (bot: 7280164925:AAHPPXH4Muq07RFMI_J5DyUhZXEo73l7LWI, chat: 7055556176)
"""

import asyncio
import time
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from telegram import Bot
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram Test Endpoint for Call Types", version="1.0.0")

# Конфигурация для предприятия 0367
BOT_TOKEN = "7280164925:AAHPPXH4Muq07RFMI_J5DyUhZXEo73l7LWI"
CHAT_ID = 374573193  # Правильный enterprise_chat_id из БД

# Глобальные переменные для хранения message_id
call_messages = {}  # unique_id -> message_id
test_message_counter = 1

async def send_or_edit_message(bot: Bot, chat_id: int, text: str, call_id: str = None) -> int:
    """Отправляет новое сообщение или редактирует существующее"""
    global call_messages
    
    if call_id and call_id in call_messages:
        # Редактируем существующее сообщение
        message_id = call_messages[call_id]
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML"
            )
            logger.info(f"✏️ Отредактировано сообщение {message_id} для {call_id}")
            return message_id
        except Exception as e:
            logger.error(f"❌ Ошибка редактирования {message_id}: {e}")
            # Создаем новое сообщение
            message = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            call_messages[call_id] = message.message_id
            return message.message_id
    else:
        # Создаем новое сообщение
        message = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        if call_id:
            call_messages[call_id] = message.message_id
        logger.info(f"📝 Создано новое сообщение {message.message_id} для {call_id}")
        return message.message_id

@app.get("/", response_class=HTMLResponse)
async def test_interface():
    """Интерфейс для тестирования всех типов звонков"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Тест Telegram сообщений по типам звонков</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .test-group {{ border: 1px solid #ccc; margin: 10px 0; padding: 15px; }}
            .test-group h3 {{ margin-top: 0; color: #333; }}
            button {{ 
                background: #007bff; color: white; border: none; 
                padding: 8px 15px; margin: 5px; border-radius: 4px; cursor: pointer; 
            }}
            button:hover {{ background: #0056b3; }}
            .status {{ margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 4px; }}
            .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <h1>🧪 Тест Telegram сообщений для бота 0367</h1>
        <div class="warning">
            <strong>Важно:</strong> Сообщения будут отправлены в чат {CHAT_ID}. 
            Каждый тест симулирует эволюцию одного сообщения через edit_message_text().
        </div>

        <div class="test-group">
            <h3>📱 ИСХОДЯЩИЕ ЗВОНКИ (CallType = 1)</h3>
            <button onclick="testCall('1-1')">Тип 1-1: Простой исходящий (ответили)</button>
            <button onclick="testCall('1-2')">Тип 1-2: Исходящий (не ответили)</button>
            <button onclick="testCall('1-3')">Тип 1-3: Исходящий с переадресацией</button>
        </div>

        <div class="test-group">
            <h3>📞 ВХОДЯЩИЕ ЗВОНКИ (CallType = 0)</h3>
            <button onclick="testCall('2-1')">Тип 2-1: Простой входящий (ответили)</button>
            <button onclick="testCall('2-2')">Тип 2-2: Входящий (не ответили)</button>
            <button onclick="testCall('2-18')">Тип 2-18: Множественный перевод A→B→C</button>
            <button onclick="testCall('2-19')">Тип 2-19: Звонок занятому менеджеру</button>
            <button onclick="testCall('2-23')">Тип 2-23: FollowMe переадресация</button>
        </div>

        <div class="test-group">
            <h3>☎️ ВНУТРЕННИЕ ЗВОНКИ (CallType = 2)</h3>
            <button onclick="testCall('3-1')">Тип 3-1: Внутренний (ответили)</button>
            <button onclick="testCall('3-2')">Тип 3-2: Внутренний (не ответили)</button>
        </div>

        <div class="test-group">
            <h3>🔧 Управление</h3>
            <button onclick="clearMessages()" style="background: #dc3545;">Очистить все message_id</button>
            <button onclick="showStatus()" style="background: #28a745;">Показать статус</button>
        </div>

        <div id="status" class="status"></div>

        <script>
            async function testCall(callType) {{
                const statusDiv = document.getElementById('status');
                statusDiv.innerHTML = `<strong>🧪 Запуск теста ${{callType}}...</strong>`;
                
                try {{
                    const response = await fetch(`/test/${{callType}}`, {{ method: 'POST' }});
                    const result = await response.json();
                    
                    if (result.status === 'success') {{
                        statusDiv.innerHTML = `
                            <strong>✅ Тест ${{callType}} завершен!</strong><br>
                            Отправлено: ${{result.messages_sent}} сообщений<br>
                            Call ID: ${{result.call_id}}<br>
                            Message ID: ${{result.final_message_id}}
                        `;
                    }} else {{
                        statusDiv.innerHTML = `<strong>❌ Ошибка теста ${{callType}}:</strong> ${{result.error}}`;
                    }}
                }} catch (error) {{
                    statusDiv.innerHTML = `<strong>❌ Ошибка:</strong> ${{error}}`;
                }}
            }}

            async function clearMessages() {{
                const response = await fetch('/clear', {{ method: 'POST' }});
                const result = await response.json();
                document.getElementById('status').innerHTML = `<strong>🧹 ${{result.message}}</strong>`;
            }}

            async function showStatus() {{
                const response = await fetch('/status');
                const result = await response.json();
                document.getElementById('status').innerHTML = `
                    <strong>📊 Статус:</strong><br>
                    Активных звонков: ${{result.active_calls}}<br>
                    Message IDs: ${{JSON.stringify(result.call_messages, null, 2)}}
                `;
            }}
        </script>
    </body>
    </html>
    """

@app.post("/test/{call_type}")
async def test_call_type(call_type: str):
    """Тестирует конкретный тип звонка"""
    global test_message_counter
    
    bot = Bot(token=BOT_TOKEN)
    call_id = f"test_{call_type}_{int(time.time())}"
    messages_sent = 0
    
    try:
        if call_type == "1-1":
            # Тип 1-1: Простой исходящий звонок (ответили)
            await send_or_edit_message(bot, CHAT_ID, 
                "☎️152 ➡️ 💰+375296123456\\nЛиния: МТС-1", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️152 📞➡️ 💰+375296123456📞", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный исходящий звонок\\n💰+375296123456\\n☎️152\\n⌛ Длительность: 03:45\\n👤 Иванов Петр Петрович\\n🔉Запись разговора", call_id)
            messages_sent += 1

        elif call_type == "1-2":
            # Тип 1-2: Исходящий звонок (не ответили)
            await send_or_edit_message(bot, CHAT_ID,
                "☎️152 ➡️ 💰+375296123456\\nЛиния: МТС-1", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "❌ Исходящий звонок не удался\\n💰+375296123456\\n☎️152\\n⌛ Длительность: 00:15", call_id)
            messages_sent += 1

        elif call_type == "1-3":
            # Тип 1-3: Исходящий с переадресацией
            await send_or_edit_message(bot, CHAT_ID,
                "☎️152 ➡️ 💰+375296123456\\nЛиния: МТС-1", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️152 📞➡️ 💰+375296123456📞", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️185 📞➡️ 💰+375296123456📞 (переведен от 152)", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный исходящий звонок\\n💰+375296123456\\n☎️185 [переведен от 152]\\n⌛ Длительность: 05:30\\n👤 Сидоров Иван Иванович\\n🔉Запись разговора", call_id)
            messages_sent += 1

        elif call_type == "2-1":
            # Тип 2-1: Простой входящий звонок (ответили)
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Приветствие\\n📡МТС Главный офис", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Доб.150,151,152\\n📡МТС Главный офис\\nЗвонил: 3 раза, Последний: 15.09.2025", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️Иванов И.И. 📞➡️ 💰+375447034448📞", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный входящий звонок\\n💰+375447034448\\n☎️Иванов И.И.\\n📡МТС Главный офис\\n⏰Начало звонка 14:25\\n⌛ Длительность: 03:45\\n👤 Петров Сергей Николаевич\\n🔉Запись разговора", call_id)
            messages_sent += 1

        elif call_type == "2-2":
            # Тип 2-2: Входящий звонок (не ответили)
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Приветствие\\n📡МТС Главный офис", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Доб.150,151,152\\n📡МТС Главный офис", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "❌ Пропущенный входящий звонок\\n💰+375447034448\\n📡МТС Главный офис\\n⏰Начало звонка 14:25\\n⌛ Длительность: 00:30", call_id)
            messages_sent += 1

        elif call_type == "2-18":
            # Тип 2-18: Множественный перевод A→B→C
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Приветствие\\n📡МТС Главный офис", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Доб.150,151,152\\n📡МТС Главный офис", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️Иванов И.И. 📞➡️ 💰+375447034448📞", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️Петров П.П. 📞➡️ 💰+375447034448📞 (переведен от Иванова)", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️Сидоров С.С. 📞➡️ 💰+375447034448📞 (переведен от Петрова)", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный входящий звонок\\n💰+375447034448\\n☎️Сидоров С.С. [3 перевода: Иванов→Петров→Сидоров]\\n📡МТС Главный офис\\n⏰Начало звонка 14:25\\n⌛ Длительность: 08:30\\n👤 Кузнецов Алексей Владимирович\\n🔉Запись разговора", call_id)
            messages_sent += 1

        elif call_type == "2-19":
            # Тип 2-19: Звонок занятому менеджеру (два сообщения)
            # Сообщение 1 - внутренний звонок
            internal_call_id = f"{call_id}_internal"
            await send_or_edit_message(bot, CHAT_ID,
                "☎️Иванов И.И. 📞➡️ ☎️185📞 (активный разговор)", internal_call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный внутренний звонок\\n☎️Иванов И.И.➡️\\n☎️185\\n⌛ Длительность: 02:30 [прерван внешним звонком]", internal_call_id)
            messages_sent += 1
            
            # Сообщение 2 - внешний звонок
            external_call_id = f"{call_id}_external"
            await asyncio.sleep(1)
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Приветствие ⚠️ЗАНЯТО\\n📡МТС Главный офис", external_call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "☎️Иванов И.И. 📞➡️ 💰+375447034448📞 (принят при занятости)", external_call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный входящий звонок\\n💰+375447034448\\n☎️Иванов И.И. [принят при занятости]\\n📡МТС Главный офис\\n⌛ Длительность: 05:20\\n👤 Смирнов Олег Петрович\\n🔉Запись разговора", external_call_id)
            messages_sent += 1

        elif call_type == "2-23":
            # Тип 2-23: FollowMe переадресация
            await send_or_edit_message(bot, CHAT_ID,
                "💰+375447034448 ➡️ Доб.150 [FollowMe]\\n📡МТС Главный офис", call_id)
            messages_sent += 1
            await asyncio.sleep(3)  # Имитируем длительную переадресацию
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный входящий звонок [FollowMe]\\n💰+375447034448\\n📞Принят на: мобильный +375296254070\\n📡МТС Главный офис\\n⏰Начало звонка 14:25\\n⌛ Длительность: 04:15\\n🔉Запись разговора", call_id)
            messages_sent += 1

        elif call_type == "3-1":
            # Тип 3-1: Простой внутренний звонок (ответили)
            await send_or_edit_message(bot, CHAT_ID,
                "☎️152 📞➡️ ☎️185📞", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "✅ Успешный внутренний звонок\\n☎️152➡️\\n☎️185\\n⌛ Длительность: 01:30", call_id)
            messages_sent += 1

        elif call_type == "3-2":
            # Тип 3-2: Внутренний звонок (не ответили)
            await send_or_edit_message(bot, CHAT_ID,
                "☎️152 📞➡️ ☎️185📞", call_id)
            messages_sent += 1
            await asyncio.sleep(2)
            
            await send_or_edit_message(bot, CHAT_ID,
                "❌ Коллега не поднял трубку\\n☎️152➡️\\n☎️185\\n⌛ Длительность: 00:15", call_id)
            messages_sent += 1

        else:
            return {"status": "error", "error": f"Неизвестный тип звонка: {call_type}"}

        final_message_id = call_messages.get(call_id)
        return {
            "status": "success",
            "call_id": call_id,
            "messages_sent": messages_sent,
            "final_message_id": final_message_id
        }

    except Exception as e:
        logger.error(f"Ошибка теста {call_type}: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/clear")
async def clear_messages():
    """Очищает все сохраненные message_id"""
    global call_messages
    call_messages.clear()
    return {"message": "Все message_id очищены"}

@app.get("/status")
async def get_status():
    """Возвращает текущий статус"""
    return {
        "active_calls": len(call_messages),
        "call_messages": call_messages
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
