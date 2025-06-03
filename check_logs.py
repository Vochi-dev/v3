import asyncio
from app.services.postgres import get_pool

async def check_logs():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем существование таблицы
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'asterisk_logs'
            );
        """)
        
        if not table_exists:
            print("❌ Таблица asterisk_logs не существует!")
            return
            
        # Получаем количество логов
        count = await conn.fetchval("SELECT COUNT(*) FROM asterisk_logs")
        print(f"\nВсего логов в базе: {count}")
        
        if count > 0:
            # Получаем последние 5 логов
            logs = await conn.fetch("""
                SELECT timestamp, unique_id, token, event_type, raw_data
                FROM asterisk_logs
                ORDER BY timestamp DESC
                LIMIT 5
            """)
            
            print("\nПоследние логи:")
            for log in logs:
                print(f"\n📞 Звонок {log['unique_id']}")
                print(f"⏰ Время: {log['timestamp']}")
                print(f"🏢 Token предприятия: {log['token']}")
                print(f"📋 Тип события: {log['event_type']}")
                print(f"📝 Данные: {log['raw_data']}")
        else:
            print("\n❌ Логов пока нет!")

if __name__ == "__main__":
    asyncio.run(check_logs()) 