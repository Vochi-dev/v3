import asyncio
from app.services.postgres import add_enterprise

async def create_test_enterprise():
    # Добавляем тестовое предприятие
    await add_enterprise(
        number="777",
        name="Тестовое предприятие",
        bot_token="test_bot_token",
        chat_id="test_chat_id",
        ip="127.0.0.1",
        secret="test_secret",
        host="test.host.com",
        name2="test_token_777"
    )
    print("Тестовое предприятие успешно добавлено!")

if __name__ == "__main__":
    asyncio.run(create_test_enterprise()) 