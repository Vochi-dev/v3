import asyncio
from app.services.postgres import init_pool, get_all_enterprises

async def test():
    await init_pool()
    enterprises = await get_all_enterprises()
    print("Enterprises:", enterprises)

if __name__ == "__main__":
    asyncio.run(test()) 