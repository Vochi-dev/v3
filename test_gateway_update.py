import asyncio
from app.services.postgres import init_pool, get_pool, update_goip_gateway, get_goip_gateway_by_id
import logging

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_update_gateway():
    await init_pool()
    pool = await get_pool()
    
    # ID существующего шлюза для тестирования
    test_gateway_id = 52
    
    test_cases = [
        ("5", "строковое число"),
        (5, "целое число"),
        ("abc", "некорректная строка"),
        (None, "None значение"),
        (0, "ноль"),
        (-1, "отрицательное число")
    ]
    
    async with pool.acquire() as conn:
        for value, description in test_cases:
            logger.info(f"\nТестируем {description}: {value}")
            try:
                # Получаем текущее состояние шлюза
                before = await get_goip_gateway_by_id(test_gateway_id)
                logger.info(f"До обновления: {before}")
                
                # Пробуем обновить
                await update_goip_gateway(
                    conn=conn,
                    gateway_id=test_gateway_id,
                    gateway_name="Test Gateway",
                    line_count=value,
                    custom_boolean_flag=True
                )
                
                # Проверяем результат
                after = await get_goip_gateway_by_id(test_gateway_id)
                logger.info(f"После обновления: {after}")
                
            except Exception as e:
                logger.error(f"Ошибка при тестировании значения {value}: {e}")

if __name__ == "__main__":
    asyncio.run(test_update_gateway()) 