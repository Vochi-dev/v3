#!/usr/bin/env python3
import asyncpg
import asyncio
import json

async def check_table_structure():
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        # Проверяем структуру таблицы user_internal_phones
        structure = await conn.fetch("""
            SELECT 
                column_name, 
                data_type, 
                is_nullable,
                column_default
            FROM information_schema.columns 
            WHERE table_name = 'user_internal_phones'
            ORDER BY ordinal_position
        """)
        
        # Проверяем индексы и ключи
        indexes = await conn.fetch("""
            SELECT 
                indexname, 
                indexdef
            FROM pg_indexes 
            WHERE tablename = 'user_internal_phones'
        """)
        
        # Проверяем существующие данные
        data = await conn.fetch("""
            SELECT user_id, enterprise_number, phone_number
            FROM user_internal_phones 
            WHERE enterprise_number = '0367'
            LIMIT 10
        """)
        
        await conn.close()
        
        result = {
            "columns": [dict(row) for row in structure],
            "indexes": [dict(row) for row in indexes],
            "sample_data": [dict(row) for row in data]
        }
        
        with open("logs/db_structure.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
            
        print("✅ Структура таблицы записана в logs/db_structure.json")
        return result
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

if __name__ == "__main__":
    asyncio.run(check_table_structure())
