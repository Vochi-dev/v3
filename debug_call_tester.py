#!/usr/bin/env python3

print("🔍 Начинаю отладку call_tester.py...")

try:
    print("📦 Импортирую модули...")
    import asyncio
    print("✅ asyncio")
    
    import asyncpg
    print("✅ asyncpg")
    
    from fastapi import FastAPI, Form, Request, HTTPException
    print("✅ FastAPI")
    
    from fastapi.templating import Jinja2Templates
    print("✅ Jinja2Templates")
    
    from fastapi.staticfiles import StaticFiles
    print("✅ StaticFiles")
    
    from datetime import datetime
    print("✅ datetime")
    
    import uvicorn
    print("✅ uvicorn")
    
    print("🔗 Тестирую подключение к БД...")
    
    async def test_db():
        try:
            conn = await asyncpg.connect(
                host="localhost",
                port=5432,
                user="postgres",
                password="r/Yskqh/ZbZuvjb2b3ahfg==",
                database="postgres"
            )
            print("✅ Подключение к БД успешно")
            
            result = await conn.fetchval("SELECT 1")
            print(f"✅ Тест запрос: {result}")
            
            await conn.close()
            print("✅ Соединение закрыто")
            
        except Exception as e:
            print(f"❌ Ошибка БД: {e}")
            return False
        return True
    
    # Запускаем тест БД
    result = asyncio.run(test_db())
    
    if result:
        print("🎉 ВСЕ МОДУЛИ И БД РАБОТАЮТ!")
    else:
        print("💥 ПРОБЛЕМА С БД!")
        
except Exception as e:
    print(f"💥 ОШИБКА ИМПОРТА: {e}")
    import traceback
    traceback.print_exc()
