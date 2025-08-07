# Система интеграций для Asterisk Webhook

## 📋 Анализ текущей ситуации

### Архитектура системы
- **Основные сервисы обработки событий**:
  - `dial.py` - обработка начала звонка (порт 8000)
  - `bridge.py` - обработка соединения абонентов
  - `hangup.py` - обработка завершения звонка  
  - `download.py` - синхронизация записей с удаленных серверов (порт 8007)

### Структура базы данных
- **Таблица `enterprises`**: содержит 32 колонки, включая:
  - Основные: `id`, `number`, `name`, `bot_token`, `chat_id`, `ip`, `secret`, `host`
  - Параметры: `parameter_option_1` до `parameter_option_5` (boolean)
  - Статус: `active`, `is_enabled`

### Текущие таблицы логов
- `asterisk_logs` - логи событий Asterisk
- `call_events` - события звонков
- `calls` - записи звонков

## 🎯 Техническое задание

### Требования
1. **Мультиинтеграция**: каждый юнит может подключить несколько CRM
2. **Асинхронная отправка**: не тормозить основной процесс
3. **Логирование**: отдельные таблицы логов для каждой CRM
4. **UI управления**: модальное окно в админпанели
5. **Различные потоки событий**:
   - `dial.py`, `bridge.py`, `hangup.py` - полный флоу звонка
   - `download.py` - только hangup без поднятия карточки

## 📊 План реализации

### 🔧 Этап 1: Структура базы данных

#### 1.1 Расширение таблицы `enterprises`
```sql
ALTER TABLE enterprises ADD COLUMN integrations_config JSONB DEFAULT '{}';
```

**Формат JSON конфигурации:**
```json
{
  "retailcrm": {
    "enabled": true,
    "api_url": "https://domain.retailcrm.ru",
    "api_key": "xxx",
    "client_id": "xxx"
  },
  "bitrix24": {
    "enabled": false,
    "webhook_url": "https://xxx.bitrix24.ru/rest/xxx/xxx/",
    "user_id": "1"
  },
  "amocrm": {
    "enabled": false,
    "subdomain": "xxx",
    "client_id": "xxx",
    "client_secret": "xxx",
    "access_token": "xxx"
  }
}
```

#### 1.2 Создание таблиц логов интеграций
```sql
-- Общая структура для всех логов интеграций
CREATE TABLE integration_logs (
    id SERIAL PRIMARY KEY,
    enterprise_number TEXT NOT NULL REFERENCES enterprises(number),
    integration_type VARCHAR(50) NOT NULL, -- 'retailcrm', 'bitrix24', 'amocrm'
    event_type VARCHAR(50) NOT NULL,       -- 'dial', 'bridge', 'hangup', 'download'
    call_unique_id TEXT,
    phone_number TEXT,
    internal_extension TEXT,
    request_data JSONB,
    response_data JSONB,
    status VARCHAR(20) NOT NULL,           -- 'success', 'error', 'pending'
    error_message TEXT,
    response_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_integration_logs_enterprise ON integration_logs(enterprise_number);
CREATE INDEX idx_integration_logs_type_status ON integration_logs(integration_type, status);
CREATE INDEX idx_integration_logs_call_id ON integration_logs(call_unique_id);
```

### 🔧 Этап 2: Модели данных и кэширование

#### 2.1 Архитектура кэширования ⚡

**Проблема**: сервисы `dial.py`, `bridge.py`, `hangup.py` обрабатывают сотни звонков в час. Обращение к БД на каждый звонок для получения конфигурации интеграций создаст узкое место.

**Решение**: In-memory кэш с автоматическим обновлением

```python
# app/services/integrations/cache.py
import asyncio
import json
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class IntegrationsCache:
    """Кэш конфигураций интеграций предприятий"""
    
    def __init__(self):
        self._cache: Dict[str, dict] = {}  # {enterprise_number: config}
        self._last_updated: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()
    
    async def get_config(self, enterprise_number: str) -> Optional[dict]:
        """Получить конфигурацию интеграций для предприятия"""
        async with self._lock:
            return self._cache.get(enterprise_number)
    
    async def update_config(self, enterprise_number: str, config: dict):
        """Обновить конфигурацию в кэше"""
        async with self._lock:
            self._cache[enterprise_number] = config
            self._last_updated[enterprise_number] = datetime.utcnow()
            logger.info(f"Обновлен кэш интеграций для {enterprise_number}")
    
    async def remove_config(self, enterprise_number: str):
        """Удалить конфигурацию из кэша"""
        async with self._lock:
            self._cache.pop(enterprise_number, None)
            self._last_updated.pop(enterprise_number, None)
    
    async def load_all_configs(self):
        """Загрузить все конфигурации из БД в кэш"""
        from app.database import get_db_connection
        
        conn = await get_db_connection()
        try:
            rows = await conn.fetch("""
                SELECT number, integrations_config 
                FROM enterprises 
                WHERE integrations_config IS NOT NULL
            """)
            
            async with self._lock:
                for row in rows:
                    enterprise_number = row['number']
                    config = json.loads(row['integrations_config']) if row['integrations_config'] else {}
                    
                    # Фильтруем только активные интеграции
                    active_config = {
                        integration_type: integration_config
                        for integration_type, integration_config in config.items()
                        if integration_config.get('enabled', False)
                    }
                    
                    if active_config:
                        self._cache[enterprise_number] = active_config
                        self._last_updated[enterprise_number] = datetime.utcnow()
            
            logger.info(f"Загружено {len(self._cache)} конфигураций интеграций в кэш")
        finally:
            await conn.close()
    
    def get_cache_stats(self) -> dict:
        """Статистика кэша для мониторинга"""
        return {
            "cached_enterprises": len(self._cache),
            "last_update": max(self._last_updated.values()) if self._last_updated else None,
            "cache_size_kb": len(str(self._cache).encode()) / 1024
        }

# Глобальный экземпляр кэша
integrations_cache = IntegrationsCache()
```

#### 2.2 Стратегия обновления кэша

```python
# app/services/integrations/cache_manager.py
import asyncio
from datetime import datetime, timedelta

class CacheManager:
    """Менеджер обновления кэша интеграций"""
    
    def __init__(self):
        self.cache = integrations_cache
        self.update_task = None
        
    async def start_cache_updater(self):
        """Запустить фоновое обновление кэша"""
        if not self.update_task:
            self.update_task = asyncio.create_task(self._cache_update_loop())
    
    async def stop_cache_updater(self):
        """Остановить фоновое обновление"""
        if self.update_task:
            self.update_task.cancel()
            self.update_task = None
    
    async def _cache_update_loop(self):
        """Фоновый цикл обновления кэша каждые 5 минут"""
        while True:
            try:
                await asyncio.sleep(300)  # 5 минут
                await self.cache.load_all_configs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка обновления кэша интеграций: {e}")
    
    async def force_reload_enterprise(self, enterprise_number: str):
        """Принудительно перезагрузить конфигурацию одного предприятия"""
        from app.database import get_db_connection
        
        conn = await get_db_connection()
        try:
            row = await conn.fetchrow("""
                SELECT integrations_config 
                FROM enterprises 
                WHERE number = $1
            """, enterprise_number)
            
            if row and row['integrations_config']:
                config = json.loads(row['integrations_config'])
                active_config = {
                    integration_type: integration_config
                    for integration_type, integration_config in config.items()
                    if integration_config.get('enabled', False)
                }
                
                if active_config:
                    await self.cache.update_config(enterprise_number, active_config)
                else:
                    await self.cache.remove_config(enterprise_number)
            else:
                await self.cache.remove_config(enterprise_number)
                
        finally:
            await conn.close()

# Глобальный менеджер кэша
cache_manager = CacheManager()
```

#### 2.3 Создание моделей Pydantic
```python
# app/models/integrations.py
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from enum import Enum

class IntegrationType(str, Enum):
    RETAILCRM = "retailcrm"
    BITRIX24 = "bitrix24"
    AMOCRM = "amocrm"

class EventType(str, Enum):
    DIAL = "dial"
    BRIDGE = "bridge"
    HANGUP = "hangup"
    DOWNLOAD = "download"

class IntegrationConfig(BaseModel):
    enabled: bool = False
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    client_id: Optional[str] = None
    # ... другие поля для каждой CRM

class CallEventData(BaseModel):
    unique_id: str
    phone_number: str
    internal_extension: Optional[str]
    call_type: str  # 'incoming', 'outgoing', 'internal'
    event_type: EventType
    raw_data: Dict[str, Any]
    enterprise_number: str
```

#### 2.2 Сервис интеграций
```python
# app/services/integrations/manager.py
import asyncio
from typing import List, Dict
from app.models.integrations import IntegrationType, CallEventData

class IntegrationManager:
    def __init__(self):
        self.handlers = {
            IntegrationType.RETAILCRM: RetailCRMHandler(),
            IntegrationType.BITRIX24: Bitrix24Handler(),
            IntegrationType.AMOCRM: AMOCRMHandler(),
        }
    
    async def send_event_async(self, enterprise_number: str, event_data: CallEventData):
        """Асинхронная отправка события во все активные интеграции"""
        # Получаем конфигурацию интеграций для предприятия
        configs = await self.get_enterprise_integrations(enterprise_number)
        
        # Создаем задачи для всех активных интеграций
        tasks = []
        for integration_type, config in configs.items():
            if config.enabled:
                handler = self.handlers.get(integration_type)
                if handler:
                    task = asyncio.create_task(
                        self._send_to_integration(
                            enterprise_number, integration_type, 
                            config, event_data
                        )
                    )
                    tasks.append(task)
        
        # Запускаем все задачи параллельно
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
```

### 🔧 Этап 3: Обработчики CRM

#### 3.1 Базовый класс
```python
# app/services/integrations/base.py
from abc import ABC, abstractmethod

class BaseIntegrationHandler(ABC):
    @abstractmethod
    async def send_dial_event(self, config, event_data):
        pass
    
    @abstractmethod  
    async def send_bridge_event(self, config, event_data):
        pass
    
    @abstractmethod
    async def send_hangup_event(self, config, event_data):
        pass
```

#### 3.2 RetailCRM обработчик
```python
# app/services/integrations/retailcrm.py
class RetailCRMHandler(BaseIntegrationHandler):
    async def send_dial_event(self, config, event_data):
        # Поднятие карточки клиента + событие начала звонка
        pass
    
    async def send_bridge_event(self, config, event_data):
        # Событие соединения
        pass
    
    async def send_hangup_event(self, config, event_data):
        # Событие завершения + загрузка записи
        pass
```

### 🔧 Этап 4: Интеграция с основными сервисами

#### 4.1 Модификация dial.py
```python
# В процесс обработки добавляем:
from app.services.integrations.manager import IntegrationManager

async def process_dial(bot: Bot, chat_id: int, data: dict):
    # ... существующий код ...
    
    # Сохраняем лог в asterisk_logs
    await save_asterisk_log(data)
    
    # НОВОЕ: Асинхронная отправка в интеграции
    integration_manager = IntegrationManager()
    event_data = CallEventData(
        unique_id=uid,
        phone_number=phone,
        internal_extension=callee,
        call_type="incoming" if not is_int else "internal",
        event_type=EventType.DIAL,
        raw_data=data,
        enterprise_number=get_enterprise_from_token(token)
    )
    
    # Не ждем результата - отправляем асинхронно
    asyncio.create_task(
        integration_manager.send_event_async(
            enterprise_number, event_data
        )
    )
    
    # ... остальной код обработки Telegram ...
```

#### 4.2 Модификация download.py
```python
# Интеграция кэша в основные сервисы

#### 4.1 Модификация dial.py
```python
# В dial.py добавляем:
from app.services.integrations.cache import integrations_cache

async def handle_dial_event(event_data: dict, enterprise_number: str):
    """Обработка события начала звонка"""
    
    # Быстрое получение конфигурации из кэша (без обращения к БД!)
    integrations_config = await integrations_cache.get_config(enterprise_number)
    
    if not integrations_config:
        # Нет активных интеграций - пропускаем
        return
    
    # Асинхронно отправляем dial событие во все активные интеграции
    from app.services.integrations.manager import IntegrationManager
    
    call_event = CallEventData(
        unique_id=event_data.get("unique_id"),
        phone_number=event_data.get("caller_id_num"),
        event_type=EventType.DIAL,
        raw_data=event_data,
        enterprise_number=enterprise_number
    )
    
    integration_manager = IntegrationManager()
    asyncio.create_task(
        integration_manager.send_event_async(
            enterprise_number, call_event, integrations_config
        )
    )
```

#### 4.2 Модификация bridge.py  
```python
# В bridge.py добавляем:
from app.services.integrations.cache import integrations_cache

async def handle_bridge_event(event_data: dict, enterprise_number: str):
    """Обработка события соединения"""
    
    # Проверяем кэш интеграций
    integrations_config = await integrations_cache.get_config(enterprise_number)
    
    if integrations_config:
        call_event = CallEventData(
            unique_id=event_data.get("unique_id"),
            phone_number=event_data.get("caller_id_num"),
            event_type=EventType.BRIDGE,
            raw_data=event_data,
            enterprise_number=enterprise_number
        )
        
        integration_manager = IntegrationManager()
        asyncio.create_task(
            integration_manager.send_event_async(
                enterprise_number, call_event, integrations_config
            )
        )
```

#### 4.3 Модификация hangup.py
```python  
# В hangup.py добавляем:
from app.services.integrations.cache import integrations_cache

async def handle_hangup_event(event_data: dict, enterprise_number: str):
    """Обработка события завершения звонка"""
    
    # Проверяем кэш интеграций
    integrations_config = await integrations_cache.get_config(enterprise_number)
    
    if integrations_config:
        call_event = CallEventData(
            unique_id=event_data.get("unique_id"),
            phone_number=event_data.get("caller_id_num"),
            event_type=EventType.HANGUP,
            raw_data=event_data,
            enterprise_number=enterprise_number
        )
        
        integration_manager = IntegrationManager()
        asyncio.create_task(
            integration_manager.send_event_async(
                enterprise_number, call_event, integrations_config
            )
        )
```

#### 4.4 Модификация download.py
```python
# В процесс синхронизации добавляем:
from app.services.integrations.cache import integrations_cache

async def process_downloaded_event(event_data):
    # ... существующий код обработки ...
    
    # Для download.py отправляем только hangup без dial/bridge
    if event_type == "hangup":
        # Проверяем кэш интеграций
        integrations_config = await integrations_cache.get_config(enterprise_number)
        
        if integrations_config:
            integration_manager = IntegrationManager()
            call_event = CallEventData(
                unique_id=uid,
                phone_number=phone,
                event_type=EventType.DOWNLOAD,  # Специальный тип
                raw_data=event_data,
                enterprise_number=enterprise_number
            )
            
            asyncio.create_task(
                integration_manager.send_event_async(
                    enterprise_number, call_event, integrations_config
                )
            )
```

#### 4.5 Инициализация кэша при старте сервисов
```python
# В main.py каждого сервиса добавляем:
from app.services.integrations.cache_manager import cache_manager

@app.on_event("startup")
async def startup_event():
    """Инициализация при старте приложения"""
    # Загружаем кэш интеграций из БД
    await cache_manager.cache.load_all_configs()
    # Запускаем фоновое обновление кэша каждые 5 минут  
    await cache_manager.start_cache_updater()
    
@app.on_event("shutdown")
async def shutdown_event():
    """Очистка при остановке приложения"""
    await cache_manager.stop_cache_updater()
```

#### 4.6 Обновление кэша при изменении настроек
```python
# В enterprise_admin_service.py
@app.post("/enterprise/{enterprise_number}/integrations/config")
async def update_integrations_config(
    enterprise_number: str, 
    request: Request,
    config: dict = Body(...)
):
    """Обновить конфигурацию интеграций"""
    session_token = request.cookies.get("session_token")
    if not await is_authorized_for_enterprise(session_token, enterprise_number):
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    # Сохраняем в БД
    conn = await get_db_connection()
    await conn.execute(
        "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
        json.dumps(config), enterprise_number
    )
    await conn.close()
    
    # ⚡ ВАЖНО: Обновляем кэш сразу же!
    from app.services.integrations.cache_manager import cache_manager
    await cache_manager.force_reload_enterprise(enterprise_number)
    
    return {"success": True}
```

### 🔧 Этап 5: UI для управления интеграциями

#### 5.1 API endpoints для интеграций
```python
# В enterprise_admin_service.py (порт 8003)
@app.get("/enterprise/{enterprise_number}/integrations/config")
async def get_integrations_config(enterprise_number: str, request: Request):
    """Получить конфигурацию интеграций для юнита"""
    # Проверка авторизации (суперадмин или админ юнита)
    session_token = request.cookies.get("session_token")
    if not await is_authorized_for_enterprise(session_token, enterprise_number):
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    # Получить integrations_config из БД
    conn = await get_db_connection()
    enterprise = await conn.fetchrow(
        "SELECT integrations_config FROM enterprises WHERE number = $1", 
        enterprise_number
    )
    return enterprise["integrations_config"] or {}

@app.post("/enterprise/{enterprise_number}/integrations/config")
async def update_integrations_config(
    enterprise_number: str, 
    request: Request,
    config: dict = Body(...)
):
    """Обновить конфигурацию интеграций"""
    session_token = request.cookies.get("session_token")
    if not await is_authorized_for_enterprise(session_token, enterprise_number):
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    # Валидация и сохранение
    conn = await get_db_connection()
    await conn.execute(
        "UPDATE enterprises SET integrations_config = $1 WHERE number = $2",
        json.dumps(config), enterprise_number
    )
    return {"success": True}

@app.get("/enterprise/{enterprise_number}/integrations/logs")
async def get_integrations_logs(enterprise_number: str, request: Request):
    """Получить логи интеграций"""
    session_token = request.cookies.get("session_token")
    if not await is_authorized_for_enterprise(session_token, enterprise_number):
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    conn = await get_db_connection()
    logs = await conn.fetch("""
        SELECT * FROM integration_logs 
        WHERE enterprise_number = $1 
        ORDER BY created_at DESC 
        LIMIT 100
    """, enterprise_number)
    return [dict(log) for log in logs]

async def is_authorized_for_enterprise(session_token: str, enterprise_number: str) -> bool:
    """Проверка: имеет ли пользователь доступ к данному предприятию"""
    if not session_token:
        return False
    
    conn = await get_db_connection()
    
    # Проверяем user_sessions (админ юнита)
    user_row = await conn.fetchrow("""
        SELECT u.enterprise_number FROM user_sessions s 
        JOIN users u ON s.user_id = u.id 
        WHERE s.session_token = $1 AND s.expires_at > NOW()
    """, session_token)
    
    if user_row and user_row["enterprise_number"] == enterprise_number:
        return True
    
    # Проверяем sessions (суперадмин - доступ ко всем)
    super_admin = await conn.fetchrow("""
        SELECT session_token FROM sessions 
        WHERE session_token = $1 AND created_at > NOW() - INTERVAL '24 hours'
    """, session_token)
    
    return bool(super_admin)
```

#### 5.2 Обновление существующей кнопки "Интеграции"
```html
<!-- Заменить в templates/enterprise_admin/dashboard.html строку 318: -->
<!-- БЫЛО: -->
<button class="btn btn-primary" type="button" onclick="alert('Раздел в разработке')">Интеграции</button>

<!-- СТАЛО: -->
<button class="btn btn-primary" type="button" onclick="openIntegrationsModal()">Интеграции</button>
```

#### 5.3 Модальное окно интеграций
```html
<!-- Добавить в конец templates/enterprise_admin/dashboard.html перед </body> -->

<!-- Модальное окно интеграций -->
<div id="integrationsModal" class="modal" style="display: none;">
    <div class="modal-content integration-modal-content">
        <div class="modal-header">
            <h2>🔗 Управление интеграциями</h2>
            <span class="close-button" onclick="closeIntegrationsModal()">&times;</span>
        </div>
        <div class="modal-body">
            <!-- Список доступных интеграций -->
            <div class="integrations-grid">
                
                <!-- RetailCRM -->
                <div class="integration-card" data-integration="retailcrm">
                    <div class="integration-header">
                        <h3>🛒 RetailCRM</h3>
                        <div class="integration-toggle">
                            <input type="checkbox" id="retailcrm_enabled" onchange="toggleIntegration('retailcrm')">
                            <label for="retailcrm_enabled">Включено</label>
                        </div>
                    </div>
                    <div class="integration-status" id="retailcrm_status">
                        <span class="status-indicator disabled">●</span>
                        <span class="status-text">Отключено</span>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="openIntegrationSettings('retailcrm')" id="retailcrm_settings_btn" disabled>
                        ⚙️ Настроить
                    </button>
                </div>

                <!-- Bitrix24 -->
                <div class="integration-card" data-integration="bitrix24">
                    <div class="integration-header">
                        <h3>📊 Bitrix24</h3>
                        <div class="integration-toggle">
                            <input type="checkbox" id="bitrix24_enabled" onchange="toggleIntegration('bitrix24')">
                            <label for="bitrix24_enabled">Включено</label>
                        </div>
                    </div>
                    <div class="integration-status" id="bitrix24_status">
                        <span class="status-indicator disabled">●</span>
                        <span class="status-text">Отключено</span>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="openIntegrationSettings('bitrix24')" id="bitrix24_settings_btn" disabled>
                        ⚙️ Настроить
                    </button>
                </div>

                <!-- AMOCRM -->
                <div class="integration-card" data-integration="amocrm">
                    <div class="integration-header">
                        <h3>💼 AMOCRM</h3>
                        <div class="integration-toggle">
                            <input type="checkbox" id="amocrm_enabled" onchange="toggleIntegration('amocrm')">
                            <label for="amocrm_enabled">Включено</label>
                        </div>
                    </div>
                    <div class="integration-status" id="amocrm_status">
                        <span class="status-indicator disabled">●</span>
                        <span class="status-text">Отключено</span>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="openIntegrationSettings('amocrm')" id="amocrm_settings_btn" disabled>
                        ⚙️ Настроить
                    </button>
                </div>

            </div>

            <!-- Логи интеграций -->
            <div class="integrations-logs">
                <h3>📋 Последние события</h3>
                <div id="integration-logs-container">
                    <p class="text-muted">Загрузка логов...</p>
                </div>
                <button class="btn btn-sm btn-outline-secondary" onclick="refreshIntegrationLogs()">
                    🔄 Обновить
                </button>
            </div>
        </div>
    </div>
</div>

<!-- Модальное окно настроек конкретной интеграции -->
<div id="integrationSettingsModal" class="modal" style="display: none;">
    <div class="modal-content">
        <div class="modal-header">
            <h2 id="settingsModalTitle">Настройки интеграции</h2>
            <span class="close-button" onclick="closeIntegrationSettingsModal()">&times;</span>
        </div>
        <div class="modal-body">
            <div id="integration-settings-content">
                <!-- Динамическое содержимое в зависимости от типа интеграции -->
            </div>
            <div class="modal-actions">
                <button class="btn btn-primary" onclick="saveIntegrationSettings()">💾 Сохранить</button>
                <button class="btn btn-secondary" onclick="testIntegrationConnection()">🔍 Тест соединения</button>
                <button class="btn btn-outline-secondary" onclick="closeIntegrationSettingsModal()">❌ Отмена</button>
            </div>
        </div>
    </div>
</div>

<style>
.integration-modal-content {
    max-width: 800px;
    width: 90%;
}

.integrations-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 20px;
    margin-bottom: 30px;
}

.integration-card {
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 15px;
    background: #f9f9f9;
}

.integration-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
}

.integration-toggle {
    display: flex;
    align-items: center;
    gap: 5px;
}

.integration-status {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-bottom: 10px;
}

.status-indicator {
    font-size: 12px;
}

.status-indicator.enabled { color: #28a745; }
.status-indicator.disabled { color: #6c757d; }
.status-indicator.error { color: #dc3545; }

.integrations-logs {
    border-top: 1px solid #ddd;
    padding-top: 20px;
}

#integration-logs-container {
    max-height: 200px;
    overflow-y: auto;
    border: 1px solid #ddd;
    padding: 10px;
    margin: 10px 0;
    background: white;
}
</style>

<script>
// Глобальная переменная для хранения конфигурации
let currentIntegrationsConfig = {};
let currentIntegrationType = null;

// Открытие основного модального окна интеграций
async function openIntegrationsModal() {
    document.getElementById('integrationsModal').style.display = 'block';
    await loadIntegrationsConfig();
    await loadIntegrationLogs();
}

function closeIntegrationsModal() {
    document.getElementById('integrationsModal').style.display = 'none';
}

// Загрузка конфигурации интеграций
async function loadIntegrationsConfig() {
    try {
        const response = await fetch(`/enterprise/{{ enterprise.number }}/integrations/config`);
        const config = await response.json();
        currentIntegrationsConfig = config;
        
        // Обновляем UI в соответствии с конфигурацией
        updateIntegrationsUI(config);
    } catch (error) {
        console.error('Ошибка загрузки конфигурации интеграций:', error);
    }
}

function updateIntegrationsUI(config) {
    ['retailcrm', 'bitrix24', 'amocrm'].forEach(type => {
        const checkbox = document.getElementById(`${type}_enabled`);
        const statusEl = document.getElementById(`${type}_status`);
        const settingsBtn = document.getElementById(`${type}_settings_btn`);
        
        const isEnabled = config[type] && config[type].enabled;
        
        checkbox.checked = isEnabled;
        settingsBtn.disabled = !isEnabled;
        
        if (isEnabled) {
            statusEl.innerHTML = '<span class="status-indicator enabled">●</span><span class="status-text">Активно</span>';
        } else {
            statusEl.innerHTML = '<span class="status-indicator disabled">●</span><span class="status-text">Отключено</span>';
        }
    });
}

// Переключение интеграции
async function toggleIntegration(type) {
    const checkbox = document.getElementById(`${type}_enabled`);
    const isEnabled = checkbox.checked;
    
    if (!currentIntegrationsConfig[type]) {
        currentIntegrationsConfig[type] = {};
    }
    
    currentIntegrationsConfig[type].enabled = isEnabled;
    
    try {
        const response = await fetch(`/enterprise/{{ enterprise.number }}/integrations/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(currentIntegrationsConfig)
        });
        
        if (response.ok) {
            updateIntegrationsUI(currentIntegrationsConfig);
            if (isEnabled) {
                // Если включили интеграцию, сразу открываем настройки
                openIntegrationSettings(type);
            }
        } else {
            alert('Ошибка сохранения настроек');
            checkbox.checked = !isEnabled; // Откат
        }
    } catch (error) {
        console.error('Ошибка:', error);
        alert('Ошибка соединения');
        checkbox.checked = !isEnabled; // Откат
    }
}

// Загрузка логов интеграций
async function loadIntegrationLogs() {
    try {
        const response = await fetch(`/enterprise/{{ enterprise.number }}/integrations/logs`);
        const logs = await response.json();
        
        const container = document.getElementById('integration-logs-container');
        if (logs.length === 0) {
            container.innerHTML = '<p class="text-muted">Логов пока нет</p>';
            return;
        }
        
        const logsHtml = logs.slice(0, 10).map(log => `
            <div class="log-entry ${log.status}">
                <span class="log-time">${new Date(log.created_at).toLocaleString()}</span>
                <span class="log-integration">${log.integration_type}</span>
                <span class="log-event">${log.event_type}</span>
                <span class="log-status">${log.status}</span>
                ${log.error_message ? `<div class="log-error">${log.error_message}</div>` : ''}
            </div>
        `).join('');
        
        container.innerHTML = logsHtml;
    } catch (error) {
        console.error('Ошибка загрузки логов:', error);
    }
}

function refreshIntegrationLogs() {
    loadIntegrationLogs();
}
</script>
```

### 🔧 Этап 6: Мониторинг и логирование

#### 6.1 Dashboard интеграций
```python
@router.get("/admin/integrations/stats")
async def get_integration_stats():
    return {
        "total_events": 1000,
        "success_rate": 98.5,
        "integrations": {
            "retailcrm": {"events": 500, "errors": 5},
            "bitrix24": {"events": 300, "errors": 2},
            "amocrm": {"events": 200, "errors": 1}
        }
    }
```

## 📝 TODO List - Трекер задач

### ✅ Анализ и планирование  
- [x] Анализ текущей архитектуры
- [x] Изучение структуры БД  
- [x] Анализ существующей кнопки "Интеграции"
- [x] Анализ системы авторизации (суперадмин vs админ юнита)
- [x] Создание обновленного технического плана

### 🗄️ База данных (2-3 дня)
- [ ] Добавить колонку `integrations_config JSONB` в таблицу `enterprises`
- [ ] Создать таблицу `integration_logs` 
- [ ] Создать индексы для производительности
- [ ] Написать миграционные скрипты
- [ ] Тестирование JSONB запросов

### 🔧 Backend сервисы (5-7 дней)
- [ ] **Создать кэш интеграций** (`app/services/integrations/cache.py`)
- [ ] **Создать менеджер кэша** (`app/services/integrations/cache_manager.py`)
- [ ] Создать модели данных (`app/models/integrations.py`)
- [ ] Реализовать `IntegrationManager` с поддержкой кэша
- [ ] Создать базовый класс `BaseIntegrationHandler`
- [ ] Реализовать `RetailCRMHandler` (на основе готового `retailcrm.py`)
- [ ] Создать заглушки для `Bitrix24Handler`, `AMOCRMHandler`
- [ ] Добавить API endpoints в `enterprise_admin_service.py`
- [ ] Реализовать функцию проверки прав `is_authorized_for_enterprise()`

### 🎯 Smart Redirect (2-3 дня) ⚡ УПРОЩЕНО
- [ ] **Добавить `ssh_smart_redirect`** в существующий `asterisk.py`
- [ ] **Создать `SmartRedirectManager`** (без AMI, через SSH)
- [ ] **Добавить метод `get_responsible_manager`** в CRM handlers
- [ ] **Создать API endpoint** `/api/smart-redirect/incoming-call`
- [ ] **Интеграция с кэшем** интеграций и вызов `asterisk.py`
- [ ] **Добавить логирование** smart redirect событий
- [ ] **Модификация диалплана** на удаленных хостах (простые изменения)

### 🔗 Интеграция с основными сервисами (3-4 дня)
- [ ] Модифицировать `app/services/calls/dial.py` - добавить чтение из кэша + асинхронные вызовы
- [ ] Модифицировать `app/services/calls/bridge.py` - добавить чтение из кэша + асинхронные вызовы  
- [ ] Модифицировать `app/services/calls/hangup.py` - добавить чтение из кэша + асинхронные вызовы
- [ ] Модифицировать `download.py` - добавить чтение из кэша + отправку hangup событий
- [ ] Добавить инициализацию кэша при старте сервисов
- [ ] Обеспечить неблокирующую работу (использование `asyncio.create_task()`)

### 🎨 Frontend (2-3 дня) ✨ УПРОЩЕНО
- [ ] Заменить `onclick="alert('Раздел в разработке')"` на `onclick="openIntegrationsModal()"`
- [ ] Добавить модальное окно интеграций в `templates/enterprise_admin/dashboard.html`
- [ ] Реализовать JavaScript функции (загрузка конфигурации, переключение, логи)
- [ ] Добавить формы настроек для каждой CRM (RetailCRM, Bitrix24, AMOCRM)
- [ ] Добавить валидацию полей и обработку ошибок

### 🧪 Тестирование (2-3 дня)
- [ ] Unit тесты для обработчиков интеграций
- [ ] Тестирование авторизации (суперадмин + админ юнита)
- [ ] Интеграционные тесты с реальными CRM
- [ ] Тестирование производительности (асинхронные вызовы)
- [ ] Тестирование UI в обоих режимах доступа

### 📈 Мониторинг и оптимизация (1-2 дня)
- [ ] Добавить метрики производительности
- [ ] Реализовать retry механизм для failed запросов  
- [ ] Добавить алерты при превышении ошибок
- [ ] Оптимизировать JSONB запросы к БД

## ⚡ Ключевые особенности реализации

### 🔐 Авторизация и безопасность
- **Двойной доступ**: функция `is_authorized_for_enterprise()` проверяет права доступа
  - **Суперадмин** (таблица `sessions`) - доступ ко всем предприятиям
  - **Админ юнита** (таблица `user_sessions`) - доступ только к своему предприятию
- **Единый UI**: одна и та же кнопка "Интеграции" работает для обоих типов пользователей
- **Безопасность API**: все endpoints проверяют session_token и права доступа

### ⚡ Асинхронность + кэширование
- Все вызовы интеграций выполняются через `asyncio.create_task()`
- **Кэш конфигураций** - сервисы dial/bridge/hangup читают из памяти, а не БД
- **Производительность**: ~0.01ms vs ~5-10ms на запрос к БД
- **Автоматическое обновление**: кэш обновляется каждые 5 минут + при изменении настроек
- Основной процесс обработки звонков не блокируется
- Asterisk получает ответ мгновенно после сохранения в локальную БД

### 📊 Масштабируемость  
- **JSONB конфигурация** позволяет легко добавлять новые CRM без ALTER TABLE
- Базовый класс `BaseIntegrationHandler` обеспечивает единообразие
- Отдельная таблица `integration_logs` предотвращает захламление основных таблиц

### 🛡️ Надежность
- Все ошибки интеграций логируются с подробной информацией
- Retry механизм для временных сбоев
- Graceful degradation при недоступности CRM
- Валидация данных на уровне Pydantic моделей

### 🎯 Удобство управления
- **Переиспользование готовой кнопки** "Интеграции" в дашборде
- Интуитивный UI с grid-макетом карточек интеграций
- Возможность включать/выключать интеграции на лету
- Подробные логи и статистика в реальном времени
- Автоматическое открытие настроек при включении интеграции

## 🎯 Умная переадресация (Smart Redirect)

### 📋 Анализ механизма работы

**Проблема**: При обычном диалплане звонок идет по заранее запрограммированному маршруту. Но нам нужно, чтобы звонок попадал к ответственному менеджеру из CRM.

**Решение**: Smart Redirect - система, которая:
1. Приостанавливает выполнение диалплана
2. Запрашивает у CRM ответственного менеджера 
3. Перенаправляет звонок на найденного менеджера

### 🔍 Анализ старой реализации

#### Входящий звонок БЕЗ Smart Redirect (0367/extensions.conf):
```bash
[dialexecute]
exten => _XXXX.,1,NoOp(Call to ${EXTEN} from ${CHANNEL(name):4:3}) and ${CALLERID(num)})
same => n,GotoIf($["${CHANNEL(name):4:3}" = "150"]?179e93af,${EXTEN},1) # прямой переход в контекст
same => n,GotoIf($["${CALLERID(num)}" = "151"]?179e93af,${EXTEN},1) 
# ... остальные проверки ...
same => n,Hangup
```

#### Входящий звонок С Smart Redirect (extensions.conf):
```bash
[dialexecute] 
# В основном контексте добавлен макрос:
same => n,Macro(incall_start,${Trunk})  # ⚡ Отправка HTTP запроса
same => n,Answer
same => n,MixMonitor(${UNIQUEID}.wav)
# Далее идет к одному из контекстов, которые завершаются:
same => n,Goto(waitredirect,${EXTEN},1)  # ⚡ Ожидание команды от сервера!

[waitredirect]
exten => _X.,1,NoOp(wait for redirect ${CHANNEL} - ${CALLERID(all)})
exten => _X.,2,Wait(10)                  # ⚡ Ждем 10 секунд команду от сервера
exten => _X.,3,Goto(apphangup,${EXTEN},1) # Если команды нет - бросаем трубку
```

### 🔄 Полный цикл Smart Redirect

#### Шаг 1: Asterisk отправляет уведомление о звонке
```bash
# Макрос incall_start 
[macro-incall_start_old]
exten => s,1,Set(API=api/callevent/start)
same => n,Set(POST={"Token":"375291111121","UniqueId":"1234567890.123","Phone":"375291234567","CallType":"0","Trunk":"0001367"})
same => n,Macro(SendCurl,${API},${BASE64_ENCODE(${POST})},incall_start,start)
```

**HTTP запрос к серверу:**
```bash
curl --location 'https://crm.vochi.by/api/callevent/start' \
     --header 'Token: "375291111121"' \
     --header 'Content-Type: application/json' \
     --data '{"Token":"375291111121","UniqueId":"1234567890.123","Phone":"375291234567","CallType":"0","Trunk":"0001367"}'
```

#### Шаг 2: Сервер ищет ответственного менеджера
**Сервер получает запрос и:**
1. Определяет предприятие по Token
2. Обращается к активным интеграциям (RetailCRM, Bitrix24, AmoCRM)
3. Ищет клиента по номеру телефона
4. Получает ответственного менеджера
5. Находит внутренний номер менеджера в системе

#### Шаг 3: Сервер отправляет команду Asterisk
**Если менеджер найден, сервер делает:**
```bash
# AMI (Asterisk Manager Interface) команда
Action: Redirect
Channel: SIP/trunk-00001234     # Канал входящего звонка
Context: web-zapros             # Специальный контекст для команд
Exten: 1                        # Добавочный номер
Priority: 1
Variable: WHO=SIP/152           # Кому перенаправить звонок
```

#### Шаг 4: Asterisk выполняет переадресацию
```bash
[web-zapros]
exten => 1,1,Dial(${WHO},,tT)   # Звонит на SIP/152 (менеджеру)
```

#### Шаг 5: Если менеджер не найден
**Если ответственный не найден или не отвечает:**
- Asterisk ждет 10 секунд в `waitredirect`
- Затем выполняет стандартный диалплан или вешает трубку

### 💡 Ключевые особенности

1. **Приостановка диалплана**: `Wait(10)` дает время серверу найти менеджера
2. **AMI команды**: Сервер управляет Asterisk через Manager Interface
3. **Токен аутентификации**: `ID_TOKEN=375291111121` для безопасности
4. **Контекст web-zapros**: Специальный контекст для команд от сервера
5. **Graceful fallback**: Если что-то не работает - звонок не теряется

### 🔧 Интеграция в новую систему

#### Новая архитектура Smart Redirect

```python
# app/services/smart_redirect/manager.py
class SmartRedirectManager:
    """Менеджер умной переадресации"""
    
    def __init__(self):
        self.ami_clients = {}  # {enterprise_number: AMIClient}
        
    async def handle_incoming_call(self, call_data: dict) -> dict:
        """Обработка входящего звонка для smart redirect"""
        
        enterprise_number = call_data["enterprise_number"]
        phone_number = call_data["phone_number"]
        unique_id = call_data["unique_id"]
        channel = call_data["channel"]
        
        # Получаем конфигурации интеграций из кэша
        integrations_config = await integrations_cache.get_config(enterprise_number)
        
        if not integrations_config:
            return {"action": "continue", "reason": "no_integrations"}
            
        # Ищем ответственного менеджера во всех активных CRM
        responsible_manager = await self._find_responsible_manager(
            phone_number, integrations_config
        )
        
        if responsible_manager:
            # Отправляем AMI команду на переадресацию
            await self._redirect_to_manager(
                enterprise_number, channel, responsible_manager
            )
            return {"action": "redirected", "manager": responsible_manager}
        else:
            return {"action": "continue", "reason": "manager_not_found"}
    
    async def _find_responsible_manager(self, phone: str, integrations: dict) -> Optional[str]:
        """Поиск ответственного менеджера в CRM"""
        
        for integration_type, config in integrations.items():
            if integration_type == "retailcrm":
                handler = RetailCRMHandler(config)
                manager = await handler.get_responsible_manager(phone)
                if manager:
                    return manager["internal_number"]
                    
            elif integration_type == "bitrix24":
                handler = Bitrix24Handler(config)
                manager = await handler.get_responsible_manager(phone)
                if manager:
                    return manager["internal_number"]
                    
            # ... остальные CRM ...
            
        return None
    
    async def _redirect_to_manager(self, enterprise: str, host_ip: str, channel: str, manager: str):
        """Отправка SSH команды на переадресацию через asterisk.py"""
        
        try:
            # Используем существующий asterisk.py сервис
            import requests
            
            response = requests.post(
                "http://localhost:8006/smart-redirect",  # порт asterisk.py
                json={
                    "host_ip": host_ip,
                    "channel": channel,
                    "target_extension": manager
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("success", False)
            else:
                logger.error(f"asterisk.py ошибка: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка вызова asterisk.py: {e}")
            return False
```

#### API endpoint для Smart Redirect

```python
# app/routers/smart_redirect.py
from app.services.smart_redirect.manager import SmartRedirectManager

smart_redirect_manager = SmartRedirectManager()

@app.post("/api/smart-redirect/incoming-call")
async def handle_smart_redirect(call_data: dict, request: Request):
    """API для обработки входящих звонков с smart redirect"""
    
    # Проверяем токен предприятия
    token = request.headers.get("Token")
    enterprise = await validate_enterprise_token(token)
    
    if not enterprise:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    call_data["enterprise_number"] = enterprise["number"]
    
    # Обрабатываем smart redirect
    result = await smart_redirect_manager.handle_incoming_call(call_data)
    
    # Логируем результат
    await log_smart_redirect_event(enterprise["number"], call_data, result)
    
    return {"success": True, "result": result}
```

#### Модификация диалплана

```bash
# В extensions.conf добавляем:
[smart-redirect-start]
exten => _X.,1,NoOp(Smart Redirect for ${CALLERID(num)} to ${EXTEN})
same => n,Set(CURL_RESULT=${SHELL(curl -X POST 'https://crm.vochi.by/api/smart-redirect/incoming-call' \
  -H 'Token: ${ID_TOKEN}' \
  -H 'Content-Type: application/json' \
  -d '{"phone":"${CALLERID(num)}","unique_id":"${UNIQUEID}","channel":"${CHANNEL}","trunk":"${EXTEN}"}')})
same => n,Goto(waitredirect,${EXTEN},1)

[waitredirect] 
exten => _X.,1,NoOp(Waiting for smart redirect command...)
exten => _X.,2,Wait(10)        # Ждем команду от сервера
exten => _X.,3,Goto(default-route,${EXTEN},1)  # Стандартный маршрут

[web-zapros]
exten => 1,1,Dial(${WHO},,tT)  # Переадресация на менеджера
```

---

## 🎯 Ожидаемые результаты

1. **Гибкая система интеграций** - каждый юнит сможет подключать нужные CRM
2. **Высокая производительность** - основной процесс не тормозится интеграциями  
3. **Надежность** - детальное логирование и обработка ошибок
4. **Масштабируемость** - легкое добавление новых CRM
5. **Удобство использования** - простой UI для настройки

**Общее время реализации: 16-24 рабочих дня**