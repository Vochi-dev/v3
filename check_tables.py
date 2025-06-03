import asyncio
from app.services.postgres import get_pool

async def check_tables():
    pool = await get_pool()
    async with pool.acquire() as conn:
        tables = await conn.fetch("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        print("Существующие таблицы:")
        for table in tables:
            print(f"- {table['table_name']}")

if __name__ == "__main__":
    asyncio.run(check_tables()) 