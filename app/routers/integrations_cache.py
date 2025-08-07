"""
API endpoints для мониторинга и управления кэшем интеграций
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from app.services.integrations.cache import integrations_cache
from app.services.integrations.cache_manager import cache_manager
from app.utils.auth import require_login

router = APIRouter()


@router.get("/cache/stats", response_class=JSONResponse)
async def get_cache_stats(request: Request):
    """Получить статистику кэша интеграций"""
    require_login(request)
    
    try:
        stats = integrations_cache.get_cache_stats()
        manager_stats = cache_manager.get_manager_stats()
        
        return {
            "success": True,
            "cache": stats,
            "manager": {
                "is_running": manager_stats["is_running"],
                "update_interval": manager_stats["update_interval"],
                "task_status": manager_stats["task_status"]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/cache/health", response_class=JSONResponse)
async def get_cache_health(request: Request):
    """Проверка состояния системы кэширования"""
    require_login(request)
    
    try:
        health = await cache_manager.health_check()
        return {"success": True, "health": health}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/cache/reload", response_class=JSONResponse)
async def reload_cache(request: Request):
    """Принудительная перезагрузка всего кэша"""
    require_login(request)
    
    try:
        await integrations_cache.load_all_configs()
        stats = integrations_cache.get_cache_stats()
        
        return {
            "success": True,
            "message": "Кэш успешно перезагружен",
            "stats": {
                "cached_enterprises": stats["cached_enterprises"],
                "total_integrations": stats["total_integrations"],
                "last_update": stats["last_update"]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/cache/reload/{enterprise_number}", response_class=JSONResponse)
async def reload_enterprise_cache(enterprise_number: str, request: Request):
    """Принудительная перезагрузка кэша для конкретного предприятия"""
    require_login(request)
    
    try:
        await cache_manager.force_reload_enterprise(enterprise_number)
        
        # Проверяем результат
        config = await integrations_cache.get_config(enterprise_number)
        
        return {
            "success": True,
            "message": f"Кэш для {enterprise_number} успешно обновлен",
            "enterprise": enterprise_number,
            "active_integrations": list(config.keys()) if config else [],
            "has_integrations": config is not None
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/cache/clear", response_class=JSONResponse)
async def clear_cache(request: Request):
    """Очистить весь кэш"""
    require_login(request)
    
    try:
        await integrations_cache.clear_cache()
        
        return {
            "success": True,
            "message": "Кэш успешно очищен"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/cache/enterprise/{enterprise_number}", response_class=JSONResponse)
async def get_enterprise_integrations(enterprise_number: str, request: Request):
    """Получить активные интеграции для предприятия"""
    require_login(request)
    
    try:
        config = await integrations_cache.get_config(enterprise_number)
        integration_types = await integrations_cache.get_integration_types(enterprise_number)
        has_integrations = await integrations_cache.has_active_integrations(enterprise_number)
        
        return {
            "success": True,
            "enterprise": enterprise_number,
            "has_integrations": has_integrations,
            "integration_types": integration_types,
            "config": config
        }
    except Exception as e:
        return {"success": False, "error": str(e)}