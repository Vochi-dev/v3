import asyncio
from app.services.postgres import get_pool

async def clear_enterprises():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute('TRUNCATE TABLE enterprises CASCADE;')
            print("Таблица enterprises успешно очищена")
    except Exception as e:
        print(f"Ошибка при очистке таблицы: {e}")

if __name__ == "__main__":
    asyncio.run(clear_enterprises()) 