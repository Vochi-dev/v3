import asyncio
import asyncpg
from app.config import settings

async def check_database():
    try:
        conn = await asyncpg.connect(
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT
        )
        
        # Проверяем текущую базу данных
        result = await conn.fetchrow("SELECT current_database()")
        print(f"Текущая база данных: {result['current_database']}")
        
        # Проверяем текущую схему
        result = await conn.fetchrow("SELECT current_schema()")
        print(f"Текущая схема: {result['current_schema']}")
        
        # Получаем список всех таблиц
        tables = await conn.fetch("""
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """)
        
        print("\nСписок таблиц:")
        for table in tables:
            print(f"{table['table_schema']}.{table['table_name']}")
            
        await conn.close()
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(check_database()) 