#!/usr/bin/env python3
"""
Простой клиент для тестирования эмулятора
"""
import httpx
import asyncio
import json
from typing import Dict, Any

class EmulatorClient:
    def __init__(self, base_url: str = "http://localhost:8030"):
        self.base_url = base_url.rstrip('/')
    
    async def health_check(self) -> bool:
        """Проверка здоровья сервиса"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/health", timeout=5.0)
                return response.status_code == 200
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return False
    
    async def get_scenarios(self) -> Dict[str, Any]:
        """Получить список доступных сценариев"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/scenarios", timeout=5.0)
                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"❌ Failed to get scenarios: {response.status_code}")
                    return {}
        except Exception as e:
            print(f"❌ Error getting scenarios: {e}")
            return {}
    
    async def run_test(self, scenario_name: str) -> Dict[str, Any]:
        """Запустить тест сценария"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/test/run/{scenario_name}", 
                    timeout=30.0
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"❌ Failed to run test {scenario_name}: {response.status_code}")
                    print(f"Response: {response.text}")
                    return {}
        except Exception as e:
            print(f"❌ Error running test {scenario_name}: {e}")
            return {}
    
    async def get_test_status(self, scenario_name: str) -> Dict[str, Any]:
        """Получить статус теста"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/test/status/{scenario_name}", 
                    timeout=5.0
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"❌ Failed to get status for {scenario_name}: {response.status_code}")
                    return {}
        except Exception as e:
            print(f"❌ Error getting status for {scenario_name}: {e}")
            return {}
    
    async def get_test_result(self, scenario_name: str) -> Dict[str, Any]:
        """Получить результат теста"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/test/result/{scenario_name}", 
                    timeout=5.0
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"❌ Failed to get result for {scenario_name}: {response.status_code}")
                    return {}
        except Exception as e:
            print(f"❌ Error getting result for {scenario_name}: {e}")
            return {}


async def main():
    """Основная функция для тестирования"""
    client = EmulatorClient()
    
    print("🔍 Checking emulator health...")
    if not await client.health_check():
        print("❌ Emulator is not healthy!")
        return
    
    print("✅ Emulator is healthy!")
    
    print("\n📋 Getting available scenarios...")
    scenarios_data = await client.get_scenarios()
    if scenarios_data and 'scenarios' in scenarios_data:
        scenarios_list = scenarios_data['scenarios']
        print(f"✅ Found {len(scenarios_list)} scenarios:")
        for scenario in scenarios_list:
            print(f"  - {scenario['test_id']}: {scenario['description']}")
        
        # Запустить тест первого сценария
        if scenarios_list:
            first_scenario = scenarios_list[0]['test_id']
        print(f"\n🚀 Running test: {first_scenario}")
        
        result = await client.run_test(first_scenario)
        if result:
            print("✅ Test started successfully!")
            print(f"Result: {json.dumps(result, indent=2, ensure_ascii=False)}")
        else:
            print("❌ Test failed to start!")
    else:
        print("❌ No scenarios found!")
        return


if __name__ == "__main__":
    asyncio.run(main())