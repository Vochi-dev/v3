#!/usr/bin/env python3
"""
Эмулятор событий звонков для тестирования системы без реальных звонков.

Воспроизводит все 42 типа звонков из справочника с корректной последовательностью событий
и проверяет результаты отправки в Telegram.
"""

import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import httpx
import os
from pathlib import Path

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class EmulatorEvent:
    """Одно событие в последовательности"""
    event_type: str  # "start", "dial", "bridge", "hangup"
    data: Dict[str, Any]
    delay_ms: int = 0  # Задержка перед отправкой (мс)

@dataclass 
class CallScenario:
    """Сценарий звонка с последовательностью событий"""
    test_id: str  # "2-1", "1-9", etc.
    description: str
    call_type: int  # 0=входящий, 1=исходящий, 2=внутренний
    complexity: str  # "SIMPLE", "MULTIPLE_TRANSFER", etc.
    events: List[EmulatorEvent]
    expected_telegram_messages: int
    expected_final_content: List[str]  # Что должно быть в финальном сообщении

class CallEmulator:
    """Основной класс эмулятора"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.scenarios: Dict[str, CallScenario] = {}
        self.test_results: Dict[str, Dict[str, Any]] = {}
        
        # Базовые данные для предприятия 0367
        self.enterprise_number = "0367"
        self.enterprise_token = "375293332255"
        
        # Загружаем сценарии
        self._load_scenarios()
    
    def _load_scenarios(self):
        """Загружает сценарии из JSON файлов"""
        templates_dir = Path(__file__).parent.parent / "event_templates"
        
        if not templates_dir.exists():
            logger.warning(f"Templates directory not found: {templates_dir}")
            return
            
        for json_file in templates_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    scenario = self._parse_scenario(data)
                    self.scenarios[scenario.test_id] = scenario
                    logger.info(f"Loaded scenario: {scenario.test_id} - {scenario.description}")
            except Exception as e:
                logger.error(f"Error loading scenario from {json_file}: {e}")
    
    def _parse_scenario(self, data: Dict[str, Any]) -> CallScenario:
        """Парсит JSON в CallScenario"""
        events = []
        for event_data in data.get("events", []):
            event = EmulatorEvent(
                event_type=event_data["type"],
                data=event_data["data"],
                delay_ms=event_data.get("delay_ms", 0)
            )
            events.append(event)
        
        return CallScenario(
            test_id=data["test_id"],
            description=data["description"],
            call_type=data["call_type"],
            complexity=data["complexity"],
            events=events,
            expected_telegram_messages=data.get("expected_telegram_messages", 1),
            expected_final_content=data.get("expected_final_content", [])
        )
    
    def _generate_unique_data(self) -> Dict[str, str]:
        """Генерирует уникальные данные для теста"""
        timestamp = int(time.time())
        unique_id = f"{timestamp}.{hash(str(uuid.uuid4())) % 100000}"
        
        return {
            "unique_id": unique_id,
            "timestamp": timestamp,
            "start_time": datetime.now().isoformat(),
            "end_time": (datetime.now() + timedelta(minutes=3)).isoformat()
        }
    
    def _inject_test_data(self, event_data: Dict[str, Any], unique_data: Dict[str, str]) -> Dict[str, Any]:
        """Инжектирует тестовые данные в событие"""
        # Копируем данные
        data = event_data.copy()
        
        # Инжектируем уникальные данные
        data["UniqueId"] = unique_data["unique_id"]
        data["Token"] = self.enterprise_token
        
        # Добавляем timestamps если нужно
        if "StartTime" in data:
            data["StartTime"] = unique_data["start_time"]
        if "EndTime" in data:
            data["EndTime"] = unique_data["end_time"]
            
        return data
    
    async def run_scenario(self, test_id: str) -> Dict[str, Any]:
        """Запускает один сценарий тестирования"""
        if test_id not in self.scenarios:
            return {"error": f"Scenario {test_id} not found"}
        
        scenario = self.scenarios[test_id]
        logger.info(f"🚀 Running scenario {test_id}: {scenario.description}")
        
        # Генерируем уникальные данные для теста
        unique_data = self._generate_unique_data()
        
        # Результат теста
        result = {
            "test_id": test_id,
            "description": scenario.description,
            "started_at": datetime.now().isoformat(),
            "events_sent": 0,
            "events_failed": 0,
            "telegram_messages": 0,
            "errors": [],
            "success": False
        }
        
        try:
            # Отправляем события по порядку с задержками
            for i, event in enumerate(scenario.events):
                # Ждем задержку
                if event.delay_ms > 0:
                    await asyncio.sleep(event.delay_ms / 1000.0)
                
                # Инжектируем тестовые данные
                event_data = self._inject_test_data(event.data, unique_data)
                
                # Отправляем событие
                success = await self._send_event(event.event_type, event_data)
                
                if success:
                    result["events_sent"] += 1
                    logger.info(f"✅ Event {i+1}/{len(scenario.events)}: {event.event_type}")
                else:
                    result["events_failed"] += 1
                    result["errors"].append(f"Failed to send {event.event_type} event")
                    logger.error(f"❌ Event {i+1}/{len(scenario.events)}: {event.event_type}")
            
            # Ждем немного для обработки последнего события
            await asyncio.sleep(1.0)
            
            # Проверяем результаты (пока заглушка)
            result["success"] = result["events_failed"] == 0
            result["completed_at"] = datetime.now().isoformat()
            
            # Сохраняем результат
            self.test_results[test_id] = result
            
            logger.info(f"✅ Scenario {test_id} completed: {result['events_sent']} events sent")
            return result
            
        except Exception as e:
            result["errors"].append(str(e))
            result["success"] = False
            result["completed_at"] = datetime.now().isoformat()
            logger.error(f"❌ Scenario {test_id} failed: {e}")
            return result
    
    async def _send_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        """Отправляет событие в сервис 8000"""
        try:
            endpoint_map = {
                "start": "/start",
                "dial": "/dial", 
                "bridge": "/bridge",
                "hangup": "/hangup"
            }
            
            endpoint = endpoint_map.get(event_type)
            if not endpoint:
                logger.error(f"Unknown event type: {event_type}")
                return False
            
            url = f"{self.base_url}{endpoint}"
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, json=data)
                
                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"HTTP {response.status_code} for {event_type}: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending {event_type} event: {e}")
            return False
    
    async def run_all_scenarios(self) -> Dict[str, Any]:
        """Запускает все сценарии"""
        logger.info(f"🚀 Running all {len(self.scenarios)} scenarios")
        
        results = {}
        total_success = 0
        
        for test_id in sorted(self.scenarios.keys()):
            result = await self.run_scenario(test_id)
            results[test_id] = result
            
            if result.get("success"):
                total_success += 1
            
            # Пауза между сценариями
            await asyncio.sleep(2.0)
        
        summary = {
            "total_scenarios": len(self.scenarios),
            "successful": total_success,
            "failed": len(self.scenarios) - total_success,
            "results": results,
            "completed_at": datetime.now().isoformat()
        }
        
        logger.info(f"✅ All scenarios completed: {total_success}/{len(self.scenarios)} successful")
        return summary
    
    def get_test_result(self, test_id: str) -> Optional[Dict[str, Any]]:
        """Получает результат теста"""
        return self.test_results.get(test_id)
    
    def list_scenarios(self) -> List[Dict[str, str]]:
        """Возвращает список доступных сценариев"""
        return [
            {
                "test_id": scenario.test_id,
                "description": scenario.description,
                "call_type": scenario.call_type,
                "complexity": scenario.complexity
            }
            for scenario in self.scenarios.values()
        ]

# Глобальный экземпляр эмулятора
emulator = CallEmulator()

if __name__ == "__main__":
    async def main():
        # Простой тест
        scenarios = emulator.list_scenarios()
        print(f"Available scenarios: {len(scenarios)}")
        for s in scenarios:
            print(f"  {s['test_id']}: {s['description']}")
    
    asyncio.run(main())
