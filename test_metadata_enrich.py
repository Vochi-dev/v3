#!/usr/bin/env python3
"""
Тестирование обогащения метаданными
"""
import asyncio
import sys
import os

# Добавляем корень проекта в PYTHONPATH
sys.path.insert(0, os.path.abspath('.'))

from app.services.metadata_client import metadata_client

async def test_enrich_message_data():
    """Тест обогащения данных сообщения"""
    
    print("🔍 Testing metadata enrichment...")
    
    # Тестовые данные для предприятия 0367
    enterprise_number = "0367"
    line_id = "0001363"
    internal_phone = "150"
    external_phone = "+375296254070"
    
    print(f"📊 Input data:")
    print(f"  enterprise_number: {enterprise_number}")
    print(f"  line_id: {line_id}")
    print(f"  internal_phone: {internal_phone}")
    print(f"  external_phone: {external_phone}")
    
    try:
        enriched_data = await metadata_client.enrich_message_data(
            enterprise_number=enterprise_number,
            line_id=line_id,
            internal_phone=internal_phone,
            external_phone=external_phone,
            short_names=False
        )
        
        print(f"\n✅ Enriched data:")
        for key, value in enriched_data.items():
            print(f"  {key}: {value}")
        
        # Проверяем ожидаемые поля
        expected_fields = ["line_name", "line_operator", "manager_name", "customer_name"]
        missing_fields = [field for field in expected_fields if field not in enriched_data]
        
        if missing_fields:
            print(f"\n⚠️  Missing expected fields: {missing_fields}")
        else:
            print(f"\n🎉 All expected fields present!")
            
        return enriched_data
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

async def test_individual_methods():
    """Тест отдельных методов"""
    
    print("\n🧪 Testing individual methods...")
    
    enterprise_number = "0367"
    line_id = "0001363"
    internal_phone = "150"
    external_phone = "+375296254070"
    
    # Тест названия линии
    line_name = await metadata_client.get_line_name(enterprise_number, line_id)
    print(f"📡 Line name: {line_name}")
    
    # Тест имени менеджера (полное)
    manager_name_full = await metadata_client.get_manager_name(enterprise_number, internal_phone, short=False)
    print(f"👤 Manager name (full): {manager_name_full}")
    
    # Тест имени менеджера (короткое)
    manager_name_short = await metadata_client.get_manager_name(enterprise_number, internal_phone, short=True)
    print(f"👤 Manager name (short): {manager_name_short}")
    
    # Тест личного номера менеджера
    personal_phone = await metadata_client.get_manager_personal_phone(enterprise_number, internal_phone)
    print(f"📱 Manager personal phone: {personal_phone}")
    
    # Тест имени клиента
    customer_name = await metadata_client.get_customer_name(enterprise_number, external_phone)
    print(f"🏢 Customer name: {customer_name}")

async def main():
    """Основная функция"""
    
    print("🚀 Starting metadata enrichment test...")
    
    # Тест обогащения данных
    enriched = await test_enrich_message_data()
    
    # Тест отдельных методов
    await test_individual_methods()
    
    print("\n🏁 Test completed!")

if __name__ == "__main__":
    asyncio.run(main())