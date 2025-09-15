#!/usr/bin/env python3
"""
Metadata Cache Service

Кэширование метаданных предприятий для быстрого доступа:
- GSM/SIP линии с названиями
- Менеджеры с ФИО и внутренними номерами 
- Данные предприятий
- Резервные линии
"""

import asyncio
import asyncpg
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)

class MetadataCache:
    """Кэш метаданных предприятий"""
    
    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.last_update: Dict[str, datetime] = {}
        
    async def load_enterprise_metadata(self, enterprise_number: str) -> bool:
        """Загружает все метаданные предприятия в кэш"""
        try:
            logger.info(f"🗄️ Загрузка метаданных для предприятия {enterprise_number}")
            
            # Инициализируем структуру кэша для предприятия
            if enterprise_number not in self.cache:
                self.cache[enterprise_number] = {}
            
            # Загружаем GSM линии
            await self._load_gsm_lines(enterprise_number)
            
            # Загружаем SIP линии  
            await self._load_sip_lines(enterprise_number)
            
            # Загружаем менеджеров
            await self._load_managers(enterprise_number)
            
            # Загружаем данные предприятия
            await self._load_enterprise_data(enterprise_number)
            
            # Загружаем резервные линии (если есть логика)
            await self._load_backup_lines(enterprise_number)
            
            self.last_update[enterprise_number] = datetime.now()
            
            logger.info(f"✅ Метаданные для предприятия {enterprise_number} загружены")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки метаданных для {enterprise_number}: {e}")
            return False
    
    async def _load_gsm_lines(self, enterprise_number: str):
        """Загружает GSM линии с названиями GoIP и торговых точек"""
        async with self.db.acquire() as conn:
            query = """
            SELECT 
                gl.line_id,
                gl.internal_id,
                gl.phone_number,
                gl.line_name,
                gl.prefix,
                g.gateway_name as goip_name,
                g.device_ip as goip_ip,
                s.name as shop_name
            FROM gsm_lines gl
            LEFT JOIN goip g ON gl.goip_id = g.id
            LEFT JOIN shop_lines sl ON gl.id = sl.gsm_line_id
            LEFT JOIN shops s ON sl.shop_id = s.id
            WHERE gl.enterprise_number = $1
            """
            
            rows = await conn.fetch(query, enterprise_number)
            
            lines = {}
            for row in rows:
                line_data = {
                    "internal_id": row['internal_id'],
                    "phone": row['phone_number'],
                    "name": row['line_name'] or f"GSM-{row['line_id']}",
                    "prefix": row['prefix'],
                    "operator": self._extract_operator(row['line_name']),
                    "goip_name": row['goip_name'],
                    "goip_ip": row['goip_ip'],
                    "shop_name": row['shop_name']
                }
                # Кэшируем только по line_id (internal_id используется для других целей)
                lines[row['line_id']] = line_data
            
            self.cache[enterprise_number]["gsm_lines"] = lines
            logger.debug(f"📱 Загружено {len(lines)} GSM линий для {enterprise_number}")
    
    async def _load_sip_lines(self, enterprise_number: str):
        """Загружает SIP линии"""
        async with self.db.acquire() as conn:
            query = """
            SELECT 
                line_name,
                prefix,
                provider_id,
                in_schema,
                out_schema
            FROM sip_unit
            WHERE enterprise_number = $1
            """
            
            rows = await conn.fetch(query, enterprise_number)
            
            sip_lines = {}
            for row in rows:
                sip_lines[row['line_name']] = {
                    "name": row['line_name'],
                    "prefix": row['prefix'],
                    "provider_id": row['provider_id'],
                    "in_schema": row['in_schema'],
                    "out_schema": row['out_schema'],
                    "type": "SIP"
                }
            
            self.cache[enterprise_number]["sip_lines"] = sip_lines
            logger.debug(f"☎️ Загружено {len(sip_lines)} SIP линий для {enterprise_number}")
    
    async def _load_managers(self, enterprise_number: str):
        """Загружает менеджеров с внутренними номерами"""
        async with self.db.acquire() as conn:
            query = """
            SELECT 
                u.id as user_id,
                u.last_name,
                u.first_name,
                u.patronymic,
                u.personal_phone,
                u.follow_me_number,
                u.follow_me_enabled,
                uip.phone_number as internal_phone,
                uip.password as internal_password
            FROM users u
            JOIN user_internal_phones uip ON u.id = uip.user_id
            WHERE u.enterprise_number = $1
            ORDER BY u.last_name, u.first_name
            """
            
            rows = await conn.fetch(query, enterprise_number)
            
            managers = {}
            for row in rows:
                # Формируем полное ФИО
                full_name_parts = [row['last_name'], row['first_name']]
                if row['patronymic']:
                    full_name_parts.append(row['patronymic'])
                full_name = ' '.join(full_name_parts)
                
                # Формируем короткое ФИО (Фамилия И.О.)
                short_name = row['last_name']
                if row['first_name']:
                    short_name += f" {row['first_name'][0]}."
                if row['patronymic']:
                    short_name += f"{row['patronymic'][0]}."
                
                manager_data = {
                    "user_id": row['user_id'],
                    "full_name": full_name,
                    "short_name": short_name,
                    "last_name": row['last_name'],
                    "first_name": row['first_name'],
                    "patronymic": row['patronymic'],
                    "personal_phone": row['personal_phone'],
                    "follow_me_number": row['follow_me_number'],
                    "follow_me_enabled": row['follow_me_enabled'],
                    "internal_password": row['internal_password']
                }
                
                managers[row['internal_phone']] = manager_data
            
            self.cache[enterprise_number]["managers"] = managers
            logger.debug(f"👥 Загружено {len(managers)} менеджеров для {enterprise_number}")
    
    async def _load_enterprise_data(self, enterprise_number: str):
        """Загружает данные предприятия"""
        async with self.db.acquire() as conn:
            query = """
            SELECT 
                number,
                name,
                name2,
                bot_token,
                chat_id,
                host,
                ip,
                secret,
                active
            FROM enterprises
            WHERE number = $1
            """
            
            row = await conn.fetchrow(query, enterprise_number)
            
            if row:
                enterprise_data = {
                    "number": row['number'],
                    "name": row['name'],
                    "name2": row['name2'],
                    "bot_token": row['bot_token'],
                    "chat_id": row['chat_id'],
                    "host": row['host'],
                    "ip": row['ip'],
                    "secret": row['secret'],
                    "active": row['active']
                }
                
                self.cache[enterprise_number]["enterprise"] = enterprise_data
                logger.debug(f"🏢 Загружены данные предприятия {enterprise_number}")
            else:
                logger.warning(f"⚠️ Предприятие {enterprise_number} не найдено в БД")
    
    async def _load_backup_lines(self, enterprise_number: str):
        """Загружает информацию о резервных линиях (пока заглушка)"""
        # TODO: Реализовать логику резервных линий если есть таблица или поле
        self.cache[enterprise_number]["backup_lines"] = {}
        logger.debug(f"🔄 Резервные линии для {enterprise_number} (пока не реализовано)")
    
    def _extract_operator(self, line_name: str) -> str:
        """Извлекает оператора из названия линии"""
        if not line_name:
            return "Unknown"
        
        line_name_upper = line_name.upper()
        if "МТС" in line_name_upper or "MTS" in line_name_upper:
            return "МТС"
        elif "A1" in line_name_upper or "VELCOM" in line_name_upper:
            return "A1"
        elif "LIFE" in line_name_upper:
            return "life:)"
        elif "SIP" in line_name_upper:
            return "SIP"
        else:
            return "GSM"
    
    # === Методы доступа к кэшу ===
    
    def get_line_name(self, enterprise_number: str, line_id: str) -> str:
        """Получает название линии по ID"""
        if enterprise_number not in self.cache:
            return f"Линия {line_id}"
        
        # Ищем в GSM линиях
        gsm_lines = self.cache[enterprise_number].get("gsm_lines", {})
        if line_id in gsm_lines:
            return gsm_lines[line_id]["name"]
        
        # Ищем в SIP линиях
        sip_lines = self.cache[enterprise_number].get("sip_lines", {})
        if line_id in sip_lines:
            return sip_lines[line_id]["name"]
        
        return f"Линия {line_id}"
    
    def get_line_operator(self, enterprise_number: str, line_id: str) -> str:
        """Получает оператора линии по ID"""
        if enterprise_number not in self.cache:
            return "Unknown"
        
        gsm_lines = self.cache[enterprise_number].get("gsm_lines", {})
        if line_id in gsm_lines:
            return gsm_lines[line_id]["operator"]
        
        sip_lines = self.cache[enterprise_number].get("sip_lines", {})
        if line_id in sip_lines:
            return "SIP"
        
        return "Unknown"
    
    def get_manager_name(self, enterprise_number: str, internal_phone: str, short: bool = False) -> str:
        """Получает ФИО менеджера по внутреннему номеру"""
        if enterprise_number not in self.cache:
            return f"Доб.{internal_phone}"
        
        managers = self.cache[enterprise_number].get("managers", {})
        if internal_phone in managers:
            if short:
                return managers[internal_phone]["short_name"]
            else:
                return managers[internal_phone]["full_name"]
        
        return f"Доб.{internal_phone}"
    
    def get_manager_personal_phone(self, enterprise_number: str, internal_phone: str) -> Optional[str]:
        """Получает мобильный номер менеджера"""
        if enterprise_number not in self.cache:
            return None
        
        managers = self.cache[enterprise_number].get("managers", {})
        if internal_phone in managers:
            return managers[internal_phone]["personal_phone"]
        
        return None
    
    def get_manager_follow_me_number(self, enterprise_number: str, internal_phone: str) -> Optional[int]:
        """Получает номер FollowMe менеджера"""
        if enterprise_number not in self.cache:
            return None
        
        managers = self.cache[enterprise_number].get("managers", {})
        if internal_phone in managers:
            return managers[internal_phone]["follow_me_number"]
        
        return None
    
    def get_manager_follow_me_enabled(self, enterprise_number: str, internal_phone: str) -> bool:
        """Проверяет включен ли FollowMe у менеджера"""
        if enterprise_number not in self.cache:
            return False
        
        managers = self.cache[enterprise_number].get("managers", {})
        if internal_phone in managers:
            return managers[internal_phone].get("follow_me_enabled", False)
        
        return False
    
    def get_manager_full_data(self, enterprise_number: str, internal_phone: str) -> Optional[Dict[str, Any]]:
        """Получает полные данные менеджера"""
        if enterprise_number not in self.cache:
            return None
        
        managers = self.cache[enterprise_number].get("managers", {})
        if internal_phone in managers:
            return managers[internal_phone].copy()  # Возвращаем копию чтобы избежать изменений
        
        return None
    
    def get_enterprise_data(self, enterprise_number: str) -> Optional[Dict[str, Any]]:
        """Получает данные предприятия"""
        if enterprise_number not in self.cache:
            return None
        
        return self.cache[enterprise_number].get("enterprise")
    
    def get_backup_lines(self, enterprise_number: str, primary_line: str) -> List[str]:
        """Получает список резервных линий"""
        if enterprise_number not in self.cache:
            return []
        
        backup_lines = self.cache[enterprise_number].get("backup_lines", {})
        return backup_lines.get(primary_line, [])
    
    def is_line_exists(self, enterprise_number: str, line_id: str) -> bool:
        """Проверяет существование линии"""
        if enterprise_number not in self.cache:
            return False
        
        gsm_lines = self.cache[enterprise_number].get("gsm_lines", {})
        sip_lines = self.cache[enterprise_number].get("sip_lines", {})
        
        return line_id in gsm_lines or line_id in sip_lines
    
    def is_manager_exists(self, enterprise_number: str, internal_phone: str) -> bool:
        """Проверяет существование менеджера"""
        if enterprise_number not in self.cache:
            return False
        
        managers = self.cache[enterprise_number].get("managers", {})
        return internal_phone in managers
    
    def get_all_internal_phones(self, enterprise_number: str) -> List[str]:
        """Получает все внутренние номера предприятия"""
        if enterprise_number not in self.cache:
            return []
        
        managers = self.cache[enterprise_number].get("managers", {})
        return list(managers.keys())
    
    def get_all_line_ids(self, enterprise_number: str) -> List[str]:
        """Получает все ID линий предприятия"""
        if enterprise_number not in self.cache:
            return []
        
        gsm_lines = self.cache[enterprise_number].get("gsm_lines", {})
        sip_lines = self.cache[enterprise_number].get("sip_lines", {})
        
        return list(gsm_lines.keys()) + list(sip_lines.keys())
    
    async def load_all_active_enterprises(self) -> int:
        """Загружает метаданные всех активных предприятий"""
        try:
            async with self.db.acquire() as conn:
                query = "SELECT number FROM enterprises WHERE active = true ORDER BY number"
                rows = await conn.fetch(query)
                
                loaded_count = 0
                for row in rows:
                    enterprise_number = row['number']
                    if await self.load_enterprise_metadata(enterprise_number):
                        loaded_count += 1
                
                logger.info(f"🗄️ Загружены метаданные для {loaded_count}/{len(rows)} предприятий")
                return loaded_count
                
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки всех предприятий: {e}")
            return 0
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Получает статистику кэша"""
        total_enterprises = len(self.cache)
        total_lines = 0
        total_managers = 0
        
        for enterprise_data in self.cache.values():
            gsm_lines = enterprise_data.get("gsm_lines", {})
            sip_lines = enterprise_data.get("sip_lines", {})
            managers = enterprise_data.get("managers", {})
            
            # Теперь считаем корректно без дублирования
            total_lines += len(gsm_lines) + len(sip_lines)
            total_managers += len(managers)
        
        return {
            "enterprises": total_enterprises,
            "total_lines": total_lines,
            "total_managers": total_managers,
            "last_updates": {k: v.isoformat() for k, v in self.last_update.items()},
            "memory_enterprises": list(self.cache.keys())
        }
    
    def clear_enterprise_cache(self, enterprise_number: str):
        """Очищает кэш конкретного предприятия"""
        if enterprise_number in self.cache:
            del self.cache[enterprise_number]
        if enterprise_number in self.last_update:
            del self.last_update[enterprise_number]
        logger.info(f"🗑️ Кэш предприятия {enterprise_number} очищен")
    
    def clear_all_cache(self):
        """Очищает весь кэш"""
        self.cache.clear()
        self.last_update.clear()
        logger.info("🗑️ Весь кэш метаданных очищен")


# === Утилиты для форматирования ===

def format_phone_display(phone: str) -> str:
    """Форматирует номер телефона для отображения по международному стандарту"""
    if not phone:
        return "Неизвестный"
    
    # Извлекаем только цифры
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) == 12 and digits.startswith('375'):
        # Белорусский номер: +375 (29) 123-45-67
        return f"+375 ({digits[3:5]}) {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif len(digits) == 11 and digits.startswith('7'):
        # Российский номер: +7 (999) 123-45-67  
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    else:
        return phone


def extract_internal_phone_from_channel(channel: str) -> Optional[str]:
    """Извлекает внутренний номер из Channel строки"""
    if not channel:
        return None
    
    # Примеры: "SIP/150-00000123", "PJSIP/151-00000456"
    parts = channel.split('/')
    if len(parts) >= 2:
        # Извлекаем номер до первого дефиса
        phone_part = parts[1].split('-')[0]
        if phone_part.isdigit():
            return phone_part
    
    return None


def extract_line_id_from_exten(exten: str) -> Optional[str]:
    """Извлекает ID линии из Exten поля"""
    # В зависимости от настроек, Exten может содержать line_id или internal_id
    return exten if exten else None

