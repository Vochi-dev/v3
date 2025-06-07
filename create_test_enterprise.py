import asyncio
from app.services.postgres import init_pool, add_enterprise

async def create_test_enterprise():
    await init_pool()
    await add_enterprise(
        number="1",
        name="Test Enterprise",
        is_enabled=1
    )
    print("Создано тестовое предприятие с номером 1")

if __name__ == "__main__":
    asyncio.run(create_test_enterprise()) 