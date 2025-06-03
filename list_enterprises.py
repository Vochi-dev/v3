import asyncio
from app.services.postgres import get_all_enterprises

async def list_all_enterprises():
    enterprises = await get_all_enterprises()
    print(f"\nНайдено предприятий: {len(enterprises)}")
    print("\nСписок предприятий:")
    for ent in enterprises:
        print(f"\nПредприятие #{ent['number']}:")
        print(f"- Название: {ent['name']}")
        print(f"- Token: {ent['name2']}")
        print(f"- IP: {ent['ip']}")
        print(f"- Host: {ent['host']}")

if __name__ == "__main__":
    asyncio.run(list_all_enterprises()) 