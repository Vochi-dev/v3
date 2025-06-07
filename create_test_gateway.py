import asyncio
from app.services.postgres import init_pool, get_pool, add_goip_gateway

async def create_test_gateway():
    await init_pool()
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        gateway_id = await add_goip_gateway(
            conn=conn,
            enterprise_number="0201",
            gateway_name="Test Gateway",
            line_count=1,
            custom_boolean_flag=True,
            config_backup_filename=None,
            config_backup_original_name=None,
            config_backup_uploaded_at=None
        )
        print(f"Создан тестовый шлюз с ID: {gateway_id}")

if __name__ == "__main__":
    asyncio.run(create_test_gateway()) 