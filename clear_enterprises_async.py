import asyncio
import asyncpg

# Параметры подключения
POSTGRES_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

async def clear_enterprises():
    try:
        # Создаем подключение
        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        
        # Выполняем очистку таблицы
        await conn.execute('TRUNCATE TABLE enterprises CASCADE;')
        print("Таблица enterprises успешно очищена")
        
    except Exception as e:
        print(f"Ошибка при очистке таблицы: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

if __name__ == "__main__":
    asyncio.run(clear_enterprises()) 