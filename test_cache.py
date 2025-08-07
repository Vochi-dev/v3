#!/usr/bin/env python3
"""
Тестирование системы кэширования интеграций
"""
import asyncio
import sys
import os

# Добавляем путь к приложению
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.integrations.cache import integrations_cache
from app.services.integrations.cache_manager import cache_manager


async def test_cache_functionality():
    """Тестирование функциональности кэша"""
    print("🧪 Тестирование кэша интеграций")
    print("=" * 50)
    
    try:
        # 1. Проверяем начальное состояние
        print("1️⃣ Начальное состояние кэша:")
        stats = integrations_cache.get_cache_stats()
        print(f"   Предприятий в кэше: {stats['cached_enterprises']}")
        print(f"   Инициализирован: {stats['is_initialized']}")
        
        # 2. Загружаем данные из БД
        print("\n2️⃣ Загрузка данных из БД...")
        await integrations_cache.load_all_configs()
        
        # 3. Проверяем результат
        print("\n3️⃣ Результат загрузки:")
        stats = integrations_cache.get_cache_stats()
        print(f"   Предприятий в кэше: {stats['cached_enterprises']}")
        print(f"   Всего интеграций: {stats['total_integrations']}")
        print(f"   Размер кэша: {stats['cache_size_kb']} KB")
        print(f"   Последнее обновление: {stats['last_update']}")
        
        # 4. Детальная информация по предприятиям
        if stats['enterprises']:
            print("\n4️⃣ Предприятия с активными интеграциями:")
            for ent_num, integrations in stats['enterprises'].items():
                print(f"   📋 {ent_num}: {integrations}")
        
        # 5. Тестируем получение конфигурации
        print("\n5️⃣ Тест получения конфигурации для 0367:")
        config = await integrations_cache.get_config("0367")
        if config:
            print(f"   ✅ Найдена конфигурация: {list(config.keys())}")
            for integration_type, integration_config in config.items():
                print(f"      🔧 {integration_type}: enabled={integration_config.get('enabled')}")
        else:
            print("   ❌ Конфигурация не найдена")
        
        # 6. Тестируем быстрые методы
        print("\n6️⃣ Быстрые проверки:")
        has_integrations = await integrations_cache.has_active_integrations("0367")
        integration_types = await integrations_cache.get_integration_types("0367")
        print(f"   Есть интеграции для 0367: {has_integrations}")
        print(f"   Типы интеграций: {integration_types}")
        
        # 7. Тестируем несуществующее предприятие
        print("\n7️⃣ Тест несуществующего предприятия:")
        config_missing = await integrations_cache.get_config("9999")
        print(f"   Конфигурация для 9999: {config_missing}")
        
        print("\n✅ Тестирование кэша завершено успешно!")
        
    except Exception as e:
        print(f"\n❌ Ошибка тестирования: {e}")
        import traceback
        traceback.print_exc()


async def test_cache_manager():
    """Тестирование менеджера кэша"""
    print("\n🎛️ Тестирование менеджера кэша")
    print("=" * 50)
    
    try:
        # 1. Проверяем статистику менеджера
        print("1️⃣ Статистика менеджера:")
        stats = cache_manager.get_manager_stats()
        print(f"   Запущен: {stats['is_running']}")
        print(f"   Интервал обновления: {stats['update_interval']}s")
        print(f"   Статус задачи: {stats['task_status']}")
        
        # 2. Прогреваем кэш
        print("\n2️⃣ Прогрев кэша...")
        await cache_manager.warm_up_cache()
        
        # 3. Тестируем принудительное обновление
        print("\n3️⃣ Принудительное обновление кэша для 0367...")
        await cache_manager.force_reload_enterprise("0367")
        
        # 4. Health check
        print("\n4️⃣ Проверка состояния системы:")
        health = await cache_manager.health_check()
        print(f"   Статус: {health['status']}")
        print(f"   Менеджер запущен: {health['manager_running']}")
        print(f"   Кэш инициализирован: {health['cache_initialized']}")
        print(f"   Кэш свежий: {health['cache_fresh']}")
        
        print("\n✅ Тестирование менеджера завершено успешно!")
        
    except Exception as e:
        print(f"\n❌ Ошибка тестирования менеджера: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Основная функция тестирования"""
    print("🚀 Запуск тестирования системы кэширования интеграций")
    print("=" * 70)
    
    await test_cache_functionality()
    await test_cache_manager()
    
    print("\n🏁 Все тесты завершены!")


if __name__ == "__main__":
    asyncio.run(main())