#!/usr/bin/env python3
"""
Тест функционала множественных номеров телефона у одного клиента

Тестирует:
1. Связывание нескольких номеров через person_uid
2. Обновление ФИО для всех номеров клиента
3. Приоритет источников данных
"""

import asyncio
import asyncpg
import json
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MultiplePhoneTestCase:
    def __init__(self):
        self.enterprise_number = "0367"
        self.test_phone_1 = "+375296254070"  # Основной номер
        self.test_phone_2 = "+375296111333"  # Дополнительный номер (для теста)
        self.pg_pool = None
        
    async def setup(self):
        """Инициализация подключения к БД"""
        try:
            self.pg_pool = await asyncpg.create_pool(
                host="127.0.0.1",
                database="postgres", 
                user="postgres",
                password="r/Yskqh/ZbZuvjb2b3ahfg==",
                min_size=1,
                max_size=3
            )
            logger.info("✅ Подключение к PostgreSQL установлено")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            raise
            
    async def cleanup(self):
        """Очистка тестовых данных"""
        if self.pg_pool:
            try:
                async with self.pg_pool.acquire() as conn:
                    # Удаляем тестовые записи
                    await conn.execute(
                        "DELETE FROM customers WHERE enterprise_number = $1 AND phone_e164 IN ($2, $3)",
                        self.enterprise_number, self.test_phone_2, self.test_phone_1
                    )
                    logger.info(f"🧹 Очищены тестовые данные")
                await self.pg_pool.close()
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при очистке: {e}")

    async def get_customers_from_db(self):
        """Получает записи клиентов из БД"""
        try:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT enterprise_number, phone_e164, first_name, last_name, 
                              middle_name, enterprise_name, meta 
                       FROM customers 
                       WHERE enterprise_number = $1 AND phone_e164 IN ($2, $3)
                       ORDER BY phone_e164""",
                    self.enterprise_number, self.test_phone_1, self.test_phone_2
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Ошибка получения данных из БД: {e}")
            return []

    async def test_case_1_create_multiple_phones_for_same_person(self):
        """Тест 1: Создание записей для нескольких номеров одного клиента"""
        logger.info("🧪 ТЕСТ 1: Создание записей для нескольких номеров одного клиента")
        
        # Очищаем существующие данные
        await self.cleanup()
        
        try:
            from app.services.customers import merge_customer_identity
            
            # Создаем запись для первого номера
            await merge_customer_identity(
                enterprise_number=self.enterprise_number,
                phone_e164=self.test_phone_1,
                source="retailcrm",
                external_id="12345",
                fio={
                    "first_name": "Степан",
                    "last_name": "Петров",
                    "middle_name": "Денисович",
                    "enterprise_name": "ООО Тест"
                },
                set_primary=True
            )
            logger.info(f"✅ Создана запись для первого номера {self.test_phone_1}")
            
            # Получаем person_uid из первой записи
            customers = await self.get_customers_from_db()
            if not customers:
                logger.error("❌ Первая запись не создана")
                return False
                
            first_customer = customers[0]
            meta = first_customer.get('meta', {})
            if isinstance(meta, str):
                meta = json.loads(meta)
            person_uid = meta.get('person_uid')
            
            if not person_uid:
                logger.error("❌ person_uid не создан для первой записи")
                return False
                
            logger.info(f"📋 Person UID: {person_uid}")
            
            # Создаем запись для второго номера с тем же person_uid
            await merge_customer_identity(
                enterprise_number=self.enterprise_number,
                phone_e164=self.test_phone_2,
                source="retailcrm",
                external_id="12345",  # Тот же ID клиента
                fio={
                    "first_name": "Степан",
                    "last_name": "Петров", 
                    "middle_name": "Денисович",
                    "enterprise_name": "ООО Тест"
                },
                set_primary=False
            )
            logger.info(f"✅ Создана запись для второго номера {self.test_phone_2}")
            
            # Проверяем результат
            customers = await self.get_customers_from_db()
            logger.info(f"📊 Найдено записей: {len(customers)}")
            
            for customer in customers:
                logger.info(f"   📞 {customer['phone_e164']}: {customer.get('last_name', '')} {customer.get('first_name', '')}")
                meta = customer.get('meta', {})
                if isinstance(meta, str):
                    meta = json.loads(meta)
                logger.info(f"      Person UID: {meta.get('person_uid', 'None')}")
                logger.info(f"      RetailCRM IDs: {meta.get('ids', {}).get('retailcrm', [])}")
            
            return len(customers) == 2
            
        except Exception as e:
            logger.error(f"❌ Ошибка в тесте 1: {e}")
            return False

    async def test_case_2_update_fio_for_all_phones(self):
        """Тест 2: Обновление ФИО обновляет все номера клиента"""
        logger.info("🧪 ТЕСТ 2: Обновление ФИО для всех номеров клиента")
        
        try:
            # Получаем текущие записи
            customers_before = await self.get_customers_from_db()
            if len(customers_before) < 2:
                logger.error("❌ Недостаточно записей для теста. Выполните тест 1 сначала.")
                return False
            
            # Получаем person_uid
            meta = customers_before[0].get('meta', {})
            if isinstance(meta, str):
                meta = json.loads(meta)
            person_uid = meta.get('person_uid')
            
            if not person_uid:
                logger.error("❌ person_uid не найден")
                return False
            
            logger.info("📝 ФИО ПЕРЕД обновлением:")
            for customer in customers_before:
                logger.info(f"   📞 {customer['phone_e164']}: {customer.get('last_name', '')} {customer.get('first_name', '')}")
            
            # Обновляем ФИО для всех номеров
            from app.services.customers import update_fio_for_person
            await update_fio_for_person(
                enterprise_number=self.enterprise_number,
                person_uid=person_uid,
                first_name="Александр",  # Изменяем имя
                last_name="Иванов",      # Изменяем фамилию
                middle_name="Петрович",  # Изменяем отчество
                enterprise_name="ООО Новое"
            )
            logger.info("✅ Обновление ФИО выполнено")
            
            # Проверяем результат
            customers_after = await self.get_customers_from_db()
            logger.info("📝 ФИО ПОСЛЕ обновления:")
            for customer in customers_after:
                logger.info(f"   📞 {customer['phone_e164']}: {customer.get('last_name', '')} {customer.get('first_name', '')}")
            
            # Проверяем что ФИО обновилось для всех записей
            all_updated = True
            for customer in customers_after:
                if customer.get('first_name') != 'Александр' or customer.get('last_name') != 'Иванов':
                    all_updated = False
                    break
            
            if all_updated:
                logger.info("✅ ФИО обновлено для всех номеров!")
                return True
            else:
                logger.warning("⚠️ ФИО обновлено не для всех номеров")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка в тесте 2: {e}")
            return False

    async def test_case_3_primary_integration_check(self):
        """Тест 3: Проверка работы с приоритетной интеграцией"""
        logger.info("🧪 ТЕСТ 3: Проверка приоритетной интеграции")
        
        try:
            # Проверяем настройки интеграции для предприятия
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT integrations_config FROM enterprises WHERE number = $1",
                    self.enterprise_number
                )
                if not row:
                    logger.error("❌ Предприятие не найдено")
                    return False
                
                integrations_config = row["integrations_config"] or {}
                primary_integration = integrations_config.get("smart", {}).get("primary")
                
                logger.info(f"🔧 Приоритетная интеграция: {primary_integration}")
                
                # Проверяем что RetailCRM является приоритетной
                if primary_integration == "retailcrm":
                    logger.info("✅ RetailCRM является приоритетной интеграцией")
                    return True
                else:
                    logger.warning(f"⚠️ Приоритетная интеграция: {primary_integration}, ожидалась retailcrm")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в тесте 3: {e}")
            return False

    async def run_all_tests(self):
        """Запускает все тесты последовательно"""
        logger.info("🚀 Запуск тестов множественных номеров")
        
        try:
            await self.setup()
            
            results = {
                "test_1_multiple_phones": await self.test_case_1_create_multiple_phones_for_same_person(),
                "test_2_fio_update": await self.test_case_2_update_fio_for_all_phones(),
                "test_3_primary_integration": await self.test_case_3_primary_integration_check()
            }
            
            logger.info("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
            for test_name, result in results.items():
                status = "✅ ПРОЙДЕН" if result else "❌ НЕ ПРОЙДЕН"
                logger.info(f"   {test_name}: {status}")
            
            all_passed = all(results.values())
            if all_passed:
                logger.info("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
            else:
                logger.warning("⚠️ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОЙДЕНЫ")
                
            return results
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при выполнении тестов: {e}")
            return None
        finally:
            # НЕ очищаем данные для анализа
            if self.pg_pool:
                await self.pg_pool.close()

async def main():
    """Основная функция для запуска тестов"""
    test_case = MultiplePhoneTestCase()
    results = await test_case.run_all_tests()
    
    if results:
        print("\n" + "="*60)
        print("ИТОГОВЫЙ ОТЧЕТ:")
        print("="*60)
        for test_name, result in results.items():
            status = "ПРОЙДЕН ✅" if result else "НЕ ПРОЙДЕН ❌"
            print(f"{test_name}: {status}")
        print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
