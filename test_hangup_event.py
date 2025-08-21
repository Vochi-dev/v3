#!/usr/bin/env python3
import aiohttp
import asyncio
import json

async def test_hangup():
    hangup_data = {
        "UniqueId": f"test-fio-update-{int(asyncio.get_event_loop().time())}",
        "Token": "375293332255",  # name2 для предприятия 0367
        "Phone": "+375296254070",
        "CallerIDNum": "+375296254070", 
        "CallStatus": "2",  # Успешный звонок
        "CallType": "0",    # Входящий
        "StartTime": "2025-08-21T06:40:00",
        "EndTime": "2025-08-21T06:41:00",
        "Extensions": ["151"],
        "Trunk": "0001363"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post("http://localhost:8000/hangup", json=hangup_data) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                print("✅ Hangup событие отправлено успешно")
            else:
                print(f"❌ Ошибка: {await resp.text()}")

if __name__ == "__main__":
    asyncio.run(test_hangup())
