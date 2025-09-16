#!/usr/bin/env python3
"""
API сервер для эмулятора событий звонков.

Предоставляет REST API для запуска тестов и получения результатов.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn

from .emulator import emulator

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Call Event Emulator API", version="1.0.0")

# Модели данных
class TestRequest(BaseModel):
    test_id: str
    enterprise_number: Optional[str] = "0367"

class BatchTestRequest(BaseModel):
    test_ids: list[str] = []  # Пустой список = все тесты
    enterprise_number: Optional[str] = "0367"

# Глобальный статус выполнения
running_tests: Dict[str, str] = {}  # test_id -> status

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    scenarios_count = len(emulator.list_scenarios())
    return {
        "status": "healthy",
        "scenarios_loaded": scenarios_count,
        "running_tests": len(running_tests)
    }

@app.get("/scenarios")
async def list_scenarios():
    """Получить список всех доступных сценариев"""
    return {
        "scenarios": emulator.list_scenarios(),
        "total": len(emulator.scenarios)
    }

@app.get("/scenarios/{test_id}")
async def get_scenario(test_id: str):
    """Получить детали конкретного сценария"""
    if test_id not in emulator.scenarios:
        raise HTTPException(status_code=404, detail=f"Scenario {test_id} not found")
    
    scenario = emulator.scenarios[test_id]
    return {
        "test_id": scenario.test_id,
        "description": scenario.description,
        "call_type": scenario.call_type,
        "complexity": scenario.complexity,
        "events_count": len(scenario.events),
        "expected_messages": scenario.expected_telegram_messages,
        "events": [
            {
                "type": event.event_type,
                "delay_ms": event.delay_ms
            }
            for event in scenario.events
        ]
    }

@app.post("/test/run/{test_id}")
async def run_single_test(test_id: str, background_tasks: BackgroundTasks):
    """Запустить один тест"""
    if test_id not in emulator.scenarios:
        raise HTTPException(status_code=404, detail=f"Scenario {test_id} not found")
    
    if test_id in running_tests:
        raise HTTPException(status_code=409, detail=f"Test {test_id} is already running")
    
    # Запускаем тест в фоне
    running_tests[test_id] = "running"
    background_tasks.add_task(run_test_background, test_id)
    
    return {
        "message": f"Test {test_id} started",
        "test_id": test_id,
        "status": "running"
    }

@app.post("/test/run-all")
async def run_all_tests(background_tasks: BackgroundTasks):
    """Запустить все тесты"""
    if "all" in running_tests:
        raise HTTPException(status_code=409, detail="Batch test is already running")
    
    running_tests["all"] = "running"
    background_tasks.add_task(run_all_tests_background)
    
    return {
        "message": "All tests started",
        "scenarios_count": len(emulator.scenarios),
        "status": "running"
    }

@app.get("/test/status/{test_id}")
async def get_test_status(test_id: str):
    """Получить статус теста"""
    if test_id in running_tests:
        return {
            "test_id": test_id,
            "status": running_tests[test_id],
            "running": True
        }
    
    # Проверяем результаты
    result = emulator.get_test_result(test_id)
    if result:
        return {
            "test_id": test_id,
            "status": "completed",
            "running": False,
            "success": result.get("success", False)
        }
    
    return {
        "test_id": test_id,
        "status": "not_found",
        "running": False
    }

@app.get("/test/result/{test_id}")
async def get_test_result(test_id: str):
    """Получить результат теста"""
    result = emulator.get_test_result(test_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Test result for {test_id} not found")
    
    return result

@app.get("/test/results")
async def get_all_results():
    """Получить все результаты тестов"""
    return {
        "results": emulator.test_results,
        "total": len(emulator.test_results)
    }

@app.delete("/test/results")
async def clear_results():
    """Очистить все результаты тестов"""
    cleared = len(emulator.test_results)
    emulator.test_results.clear()
    return {
        "message": f"Cleared {cleared} test results"
    }

@app.get("/test/running")
async def get_running_tests():
    """Получить список запущенных тестов"""
    return {
        "running_tests": running_tests,
        "count": len(running_tests)
    }

# Фоновые задачи
async def run_test_background(test_id: str):
    """Фоновая задача для запуска одного теста"""
    try:
        logger.info(f"Starting background test: {test_id}")
        result = await emulator.run_scenario(test_id)
        
        if result.get("success"):
            running_tests[test_id] = "completed"
            logger.info(f"✅ Test {test_id} completed successfully")
        else:
            running_tests[test_id] = "failed"
            logger.error(f"❌ Test {test_id} failed")
            
    except Exception as e:
        running_tests[test_id] = "error"
        logger.error(f"💥 Test {test_id} crashed: {e}")
    finally:
        # Убираем из running через некоторое время
        await asyncio.sleep(5)
        running_tests.pop(test_id, None)

async def run_all_tests_background():
    """Фоновая задача для запуска всех тестов"""
    try:
        logger.info("Starting background batch test")
        result = await emulator.run_all_scenarios()
        
        successful = result.get("successful", 0)
        total = result.get("total_scenarios", 0)
        
        if successful == total:
            running_tests["all"] = "completed"
            logger.info(f"✅ All tests completed successfully: {successful}/{total}")
        else:
            running_tests["all"] = "partial"
            logger.warning(f"⚠️ Tests completed with failures: {successful}/{total}")
            
    except Exception as e:
        running_tests["all"] = "error"
        logger.error(f"💥 Batch test crashed: {e}")
    finally:
        # Убираем из running через некоторое время
        await asyncio.sleep(10)
        running_tests.pop("all", None)

if __name__ == "__main__":
    uvicorn.run(
        "tests.call_emulator.api_server:app",
        host="0.0.0.0",
        port=8030,
        log_level="info",
        reload=False
    )
