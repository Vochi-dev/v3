import asyncpg
from typing import List, Dict, Optional
from datetime import datetime
import logging
import sys
import os
from pydantic import BaseModel
from fastapi import HTTPException

# Конфигурация логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(console_handler)

# Конфигурация подключения
POSTGRES_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'database': 'postgres'
}

# Глобальный пул подключений
_pool = None

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'postgres.log')

class GsmLine(BaseModel):
    id: int
    goip_id: int
    enterprise_number: str
    line_id: str
    internal_id: str
    line_name: Optional[str] = None
    phone_number: Optional[str] = None
    prefix: Optional[str] = None
    slot: Optional[int] = None
    created_at: datetime
    goip_name: Optional[str] = None

async def init_pool():
    """Инициализирует глобальный пул подключений"""
    global _pool
    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(
                user=POSTGRES_CONFIG['user'],
                password=POSTGRES_CONFIG['password'],
                database=POSTGRES_CONFIG['database'],
                host=POSTGRES_CONFIG['host'],
                port=POSTGRES_CONFIG['port'],
                min_size=2,
                max_size=10
            )
            logger.info("Connection pool created successfully.")
        except Exception as e:
            logger.critical(f"Failed to create connection pool: {e}")
            # В случае критической ошибки можно завершить работу или предпринять другие действия
            _pool = None
            raise

async def get_pool():
    """Возвращает существующий пул подключений или создает новый"""
    if _pool is None:
        await init_pool()
    return _pool

async def close_pool():
    """Закрывает пул подключений"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

async def get_db():
    """FastAPI-зависимость для получения соединения из пула."""
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Connection pool is not initialized")
    async with pool.acquire() as conn:
        yield conn

# Функции для работы с предприятиями
async def get_all_enterprises():
    """Получает список всех предприятий"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT number, name, bot_token, chat_id, ip, secret, host, 
                   created_at, name2, active, is_enabled,
                   scheme_count, gsm_line_count, 
                   parameter_option_1, parameter_option_2, parameter_option_3,
                   parameter_option_4, parameter_option_5,
                   custom_domain, custom_port
            FROM enterprises
            ORDER BY CAST(number AS INTEGER) ASC
        """)
        return [dict(row) for row in rows]

async def get_enterprise_by_number(number: str):
    """Получает предприятие по номеру"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql_query = """
            SELECT number, name, bot_token, chat_id, ip, secret, host,
                   created_at, name2, active, is_enabled,
                   scheme_count, gsm_line_count, 
                   parameter_option_1, parameter_option_2, parameter_option_3,
                   parameter_option_4, parameter_option_5,
                   custom_domain, custom_port
            FROM enterprises
            WHERE number = $1
            LIMIT 1
        """
        row = await conn.fetchrow(sql_query, number)
        if row:
            return dict(row)
        else:
            return None

async def add_enterprise(number: str, name: str, bot_token: str, chat_id: str,
                        ip: str, secret: str, host: str, name2: str = '',
                        is_enabled: bool = True,
                        active: bool = True,
                        scheme_count: Optional[int] = None, 
                        gsm_line_count: Optional[int] = None,
                        parameter_option_1: bool = False, 
                        parameter_option_2: bool = False,
                        parameter_option_3: bool = False, 
                        parameter_option_4: bool = False,
                        parameter_option_5: bool = False,
                        custom_domain: Optional[str] = None,
                        custom_port: Optional[int] = None):
    """Добавляет новое предприятие"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Создаем предприятие
        await conn.execute("""
            INSERT INTO enterprises (
                number, name, bot_token, chat_id, ip, secret, host, 
                created_at, name2, is_enabled, scheme_count, gsm_line_count,
                parameter_option_1, parameter_option_2, parameter_option_3,
                parameter_option_4, parameter_option_5, custom_domain, custom_port,
                active
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
        """, number, name, bot_token, chat_id, ip, secret, host,
        datetime.utcnow(), name2, is_enabled, scheme_count, gsm_line_count,
        parameter_option_1, parameter_option_2, parameter_option_3,
        parameter_option_4, parameter_option_5, custom_domain, custom_port,
        active)
        
        # Автоматическое создание подписчиков в telegram_users
        # если chat_id отличается от 374573193 и bot_token не пустой
        if chat_id and chat_id != "374573193" and bot_token and bot_token.strip():
            await _auto_create_telegram_subscribers(conn, bot_token, chat_id)

def debug_log(message):
    os.system(f'echo "{message}" >> /root/asterisk-webhook/debug.txt')

async def update_enterprise(number: str, name: str, bot_token: str, chat_id: str,
                          ip: str, secret: str, host: str, name2: str = '',
                          active: Optional[bool] = None,
                          is_enabled: Optional[bool] = None,
                          scheme_count: Optional[int] = None, 
                          gsm_line_count: Optional[int] = None,
                          parameter_option_1: bool = False, 
                          parameter_option_2: bool = False,
                          parameter_option_3: bool = False, 
                          parameter_option_4: bool = False,
                          parameter_option_5: bool = False,
                          custom_domain: Optional[str] = None,
                          custom_port: Optional[int] = None):
    """Обновляет информацию о предприятии"""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Обновляем предприятие
            update_fields = {
                "name": name, "bot_token": bot_token, "chat_id": chat_id,
                "ip": ip, "secret": secret, "host": host, "name2": name2,
                "scheme_count": scheme_count, "gsm_line_count": gsm_line_count,
                "parameter_option_1": parameter_option_1,
                "parameter_option_2": parameter_option_2,
                "parameter_option_3": parameter_option_3,
                "parameter_option_4": parameter_option_4,
                "parameter_option_5": parameter_option_5,
                "custom_domain": custom_domain, "custom_port": custom_port
            }
            if active is not None:
                update_fields["active"] = active
            if is_enabled is not None:
                update_fields["is_enabled"] = is_enabled

            set_clauses = []
            values = []
            for i, (key, value) in enumerate(update_fields.items()):
                set_clauses.append(f"{key} = ${i+1}")
                values.append(value)
            
            values.append(number) # Для WHERE number = $N

            sql_query = f"""
                UPDATE enterprises
                SET {", ".join(set_clauses)}
                WHERE number = ${len(values)}
            """
            result = await conn.execute(sql_query, *values)
            
            # Автоматическое создание подписчиков в telegram_users
            # если chat_id отличается от 374573193 и bot_token не пустой
            if chat_id and chat_id != "374573193" and bot_token and bot_token.strip():
                await _auto_create_telegram_subscribers(conn, bot_token, chat_id)
                
    except Exception as e:
        print(f"POSTGRES ERROR: {str(e)}")
        raise


async def _auto_create_telegram_subscribers(conn, bot_token: str, chat_id: str):
    """Автоматически создаёт подписчиков в telegram_users"""
    try:
        # Проверяем существующих подписчиков для этого bot_token
        existing_subscribers = await conn.fetch(
            "SELECT tg_id FROM telegram_users WHERE bot_token = $1",
            bot_token
        )
        existing_tg_ids = {str(row['tg_id']) for row in existing_subscribers}
        
        # Подписчики, которых нужно добавить
        subscribers_to_add = []
        
        # Всегда должен быть суперюзер 374573193
        if "374573193" not in existing_tg_ids:
            subscribers_to_add.append(("374573193", "support@example.com"))
            
        # Добавляем новый chat_id если его нет
        if chat_id not in existing_tg_ids:
            subscribers_to_add.append((chat_id, "support2@example.com"))
        
        # Создаём записи
        for tg_id, email in subscribers_to_add:
            try:
                await conn.execute(
                    "INSERT INTO telegram_users (tg_id, email, bot_token) VALUES ($1, $2, $3)",
                    int(tg_id), email, bot_token
                )
                print(f"Auto-created telegram subscriber: tg_id={tg_id}, bot_token={bot_token}")
            except Exception as e:
                # Игнорируем ошибки дублирования, но логируем другие
                if "duplicate key" not in str(e).lower():
                    print(f"Error creating telegram subscriber {tg_id}: {e}")
                    
    except Exception as e:
        print(f"Error in _auto_create_telegram_subscribers: {e}")
        # Не прерываем основную операцию из-за ошибок в подписчиках

async def delete_enterprise(number: str):
    """Удаляет предприятие"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM enterprises WHERE number = $1", number)

async def delete_goip_gateway(gateway_id: int):
    """
    Удаляет шлюз и все связанные с ним GSM-линии в одной транзакции.
    """
    print(f"POSTGRES_SERVICE: Попытка удаления шлюза с ID: {gateway_id}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Сначала удаляем связанные линии
                deleted_lines = await conn.execute("DELETE FROM gsm_lines WHERE goip_id = $1", gateway_id)
                print(f"POSTGRES_SERVICE: Удалено связанных GSM-линий: {deleted_lines}")
                
                # Затем удаляем сам шлюз
                deleted_gateway = await conn.execute("DELETE FROM goip WHERE id = $1", gateway_id)
                print(f"POSTGRES_SERVICE: Удален шлюз: {deleted_gateway}")

                if '0' in deleted_gateway:
                     print(f"POSTGRES_SERVICE: Шлюз с ID {gateway_id} не найден для удаления.")
                     raise HTTPException(status_code=404, detail="Шлюз не найден.")

            except Exception as e:
                print(f"POSTGRES_SERVICE: Ошибка при удалении шлюза ID {gateway_id}: {e}")
                # Транзакция будет автоматически откатана
                raise HTTPException(status_code=500, detail=f"Ошибка базы данных при удалении шлюза: {e}")

async def get_enterprises_with_tokens():
    """Получает список предприятий с активными токенами"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT number, name, bot_token, chat_id, ip, secret, host,
                   created_at, name2, active, is_enabled,
                   scheme_count, gsm_line_count, 
                   parameter_option_1, parameter_option_2, parameter_option_3,
                   parameter_option_4, parameter_option_5,
                   custom_domain, custom_port
            FROM enterprises
            WHERE bot_token IS NOT NULL 
              AND chat_id IS NOT NULL 
              AND TRIM(bot_token) != '' 
              AND TRIM(chat_id) != ''
              AND active = TRUE
              AND is_enabled = TRUE
            ORDER BY CAST(number AS INTEGER) ASC
        """)
        return [dict(row) for row in rows]

async def get_enterprise_number_by_bot_token(bot_token: str) -> str:
    """Получает номер предприятия по токену бота"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT number FROM enterprises WHERE bot_token = $1",
            bot_token
        )
        return row['number'] if row else None 

async def get_enterprise_by_name2_suffix(name2_suffix: str) -> Optional[Dict]:
    """
    Получает предприятие, у которого поле name2 заканчивается на указанный суффикс.
    Возвращает первую найденную запись или None.
    """
    print(f"POSTGRES_GET_BY_NAME2_SUFFIX: Вызвана для суффикса: '{name2_suffix}'", file=sys.stderr, flush=True)
    pool = await get_pool()
    if not pool:
        print("POSTGRES_GET_BY_NAME2_SUFFIX ERROR: Пул не инициализирован", file=sys.stderr, flush=True)
        return None
        
    async with pool.acquire() as conn:
        sql_query = """
            SELECT number, name, bot_token, chat_id, name2, active, is_enabled,
                   scheme_count, gsm_line_count, 
                   parameter_option_1, parameter_option_2, parameter_option_3,
                   parameter_option_4, parameter_option_5,
                   custom_domain, custom_port
            FROM enterprises
            WHERE name2 LIKE $1
            ORDER BY id ASC  -- Или другая логика сортировки, если нужно выбрать конкретное из нескольких
            LIMIT 1
        """
        # Для LIKE '%suffix' параметр должен быть '%suffix'
        param_suffix = '%' + name2_suffix
        print(f"POSTGRES_GET_BY_NAME2_SUFFIX: Выполняется SQL: {sql_query.strip()} с параметром: '{param_suffix}'", file=sys.stderr, flush=True)
        row = await conn.fetchrow(sql_query, param_suffix)
        print(f"POSTGRES_GET_BY_NAME2_SUFFIX: Результат fetchrow: {row}", file=sys.stderr, flush=True)
        if row:
            return dict(row)
        else:
            return None 

async def get_gateways_by_enterprise_number(enterprise_number: str) -> List[Dict]:
    """Получает список шлюзов для указанного предприятия"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, gateway_name, line_count, config_backup_original_name, config_backup_uploaded_at, custom_boolean_flag
            FROM goip
            WHERE enterprise_number = $1
            ORDER BY id
        """, enterprise_number)
        return [dict(row) for row in rows]

async def get_goip_gateway_by_id(gateway_id: int) -> Optional[Dict]:
    """Получает шлюз по его ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM goip WHERE id = $1", gateway_id)
        return dict(row) if row else None

async def _get_max_line_id(conn, enterprise_number: str) -> int:
    """
    Получает максимальный line_id из таблицы gsm_lines для конкретного предприятия.
    """
    max_id_str = await conn.fetchval(
        "SELECT MAX(line_id) FROM gsm_lines WHERE enterprise_number = $1",
        enterprise_number
    )
    return int(max_id_str) if max_id_str and max_id_str.isdigit() else 0

async def _get_enterprise_name2_suffix(conn, enterprise_number: str) -> str:
    """
    Получает последние 4 цифры name2 для предприятия.
    Если name2 пустое или нет цифр, возвращает номер предприятия.
    """
    name2 = await conn.fetchval(
        "SELECT name2 FROM enterprises WHERE number = $1",
        enterprise_number
    )
    if name2 and len(name2) >= 4:
        # Извлекаем только цифры из name2
        digits_only = ''.join(filter(str.isdigit, name2))
        if len(digits_only) >= 4:
            return digits_only[-4:]  # Последние 4 цифры
    
    # Если не удалось получить 4 цифры из name2, используем номер предприятия
    return enterprise_number

async def _get_last_internal_id_details(conn, enterprise_number: str) -> tuple[int, int, str]:
    """
    Получает детали последнего internal_id для предприятия.
    Возвращает кортеж (номер_блока, номер_линии_в_блоке, name2_suffix).
    """
    # Получаем последние 4 цифры name2 для этого предприятия
    name2_suffix = await _get_enterprise_name2_suffix(conn, enterprise_number)
    
    # Ищем последний internal_id для предприятия с учетом нового формата
    last_internal_id = await conn.fetchval(
        "SELECT internal_id FROM gsm_lines WHERE enterprise_number = $1 ORDER BY internal_id DESC LIMIT 1",
        enterprise_number
    )
    if not last_internal_id:
        return 10, 0, name2_suffix # Если линий нет, начинаем с 10-го блока, 0-й линии

    # Парсим ID: первые 2 символа - блок, символы 3-6 - код из name2, последние 2 - номер линии
    try:
        block_part_str = last_internal_id[:2]
        name2_part_str = last_internal_id[2:6]
        line_in_block_str = last_internal_id[6:8]
        
        block_part = int(block_part_str)
        line_in_block = int(line_in_block_str)
        
        # Проверяем, соответствует ли найденный ID новому формату
        if name2_part_str == name2_suffix:
            return block_part, line_in_block, name2_suffix
        else:
            # Если старый формат, начинаем заново с новым форматом
            return 10, 0, name2_suffix
            
    except (ValueError, TypeError, IndexError):
        # В случае ошибки парсинга, возвращаем значения по умолчанию
        return 10, 0, name2_suffix

async def _add_gsm_line(conn, goip_id, enterprise_number, line_id, internal_id, prefix):
    await conn.execute(
        """
        INSERT INTO gsm_lines (goip_id, enterprise_number, line_id, internal_id, prefix, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        goip_id, enterprise_number, line_id, internal_id, prefix, datetime.utcnow()
    )

async def create_gsm_lines_for_gateway(conn: asyncpg.Connection, gateway_id: int, gateway_name: str, enterprise_number: str, line_count: int):
    """
    Создает указанное количество GSM-линий для шлюза,
    используя следующий доступный line_id и internal_id для предприятия.
    """
    # Шаг 1: Получаем последний (максимальный) line_id для ЭТОГО предприятия
    last_line_id = await _get_max_line_id(conn, enterprise_number)
    
    # Шаг 2: Получаем последний internal_id и последние 4 цифры name2 для ЭТОГО предприятия
    block, last_line_in_block, name2_suffix = await _get_last_internal_id_details(conn, enterprise_number)

    # Определяем начальные значения
    next_line_id = 1363 if last_line_id == 0 else last_line_id + 1
    
    # Для КАЖДОГО нового шлюза нумерация префиксов начинается с 21.
    current_prefix = 21

    # Шаг 3: Создаем линии в цикле
    current_block = block
    current_line_in_block = last_line_in_block
    
    for i in range(line_count):
        new_line_id = next_line_id + i
        
        # Корректная логика генерации internal_id
        current_line_in_block += 1
        if current_line_in_block > 99:
            current_block += 1
            current_line_in_block = 1
        
        line_in_block_str = str(current_line_in_block).zfill(2)
        # Используем последние 4 цифры name2 вместо номера предприятия
        new_internal_id = f"{current_block:02d}{name2_suffix}{line_in_block_str}"

        await _add_gsm_line(
            conn=conn,
            goip_id=gateway_id,
            enterprise_number=enterprise_number,
            line_id=f"{new_line_id:07d}",
            internal_id=new_internal_id,
            prefix=str(current_prefix)
        )
        current_prefix += 1
    
    logger.info(f"Успешно создано {line_count} линий для шлюза ID {gateway_id} предприятия {enterprise_number}.")

async def add_goip_gateway(conn: asyncpg.Connection, enterprise_number: str, gateway_name: str, line_count: Optional[int],
                           custom_boolean_flag: Optional[bool] = False) -> Dict:
    """
    Добавляет новый шлюз, ИСПОЛЬЗУЯ СУЩЕСТВУЮЩЕЕ ПОДКЛЮЧЕНИЕ.
    Возвращает словарь с данными нового шлюза.
    Логика создания линий теперь вынесена в роутер.
    """
    sql_query = """
        INSERT INTO goip (enterprise_number, gateway_name, line_count, 
                                   custom_boolean_flag, created_at)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, gateway_name, line_count, created_at
    """
    
    inserted_gateway = await conn.fetchrow(
        sql_query,
        enterprise_number, gateway_name, line_count,
        custom_boolean_flag,
        datetime.utcnow()
    )
    return dict(inserted_gateway)

async def update_goip_gateway(gateway_id: int, gateway_name: Optional[str] = None, line_count: Optional[int] = None,
                              config_backup_filename: Optional[str] = None,
                              config_backup_original_name: Optional[str] = None,
                              config_backup_uploaded_at: Optional[datetime] = None,
                              custom_boolean_flag: Optional[bool] = None):
    """Обновляет информацию о шлюзе."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        update_fields = {}
        if gateway_name is not None: update_fields['gateway_name'] = gateway_name
        if line_count is not None: update_fields['line_count'] = line_count
        if config_backup_filename is not None: update_fields['config_backup_filename'] = config_backup_filename
        if config_backup_original_name is not None: update_fields['config_backup_original_name'] = config_backup_original_name
        if config_backup_uploaded_at is not None: update_fields['config_backup_uploaded_at'] = config_backup_uploaded_at
        if custom_boolean_flag is not None: update_fields['custom_boolean_flag'] = custom_boolean_flag
        
        if not update_fields:
            return None 

        set_clauses = [f"{key} = ${i+1}" for i, key in enumerate(update_fields.keys())]
        values = list(update_fields.values())
        
        query = f"UPDATE goip SET {', '.join(set_clauses)} WHERE id = ${len(values) + 1}"
        
        values.append(gateway_id)
        
        await conn.execute(query, *values)
        
        # Возвращаем обновленные данные
        return await conn.fetchrow("SELECT * FROM goip WHERE id = $1", gateway_id)

# Функции для работы с мобильными операторами

async def get_all_mobile_operators() -> list[dict]:
    """Возвращает список всех мобильных операторов."""
    pool = await get_pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch("SELECT id, name, shablon FROM mobile ORDER BY id ASC")
        return [dict(row) for row in rows]

async def add_mobile_operator(name: str, shablon: str) -> Dict:
    """Добавляет нового мобильного оператора и возвращает его."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO mobile (name, shablon) VALUES ($1, $2) RETURNING id, name, shablon",
            name, shablon
        )
        return dict(row)

async def update_mobile_operator(operator_id: int, name: str, shablon: str) -> dict:
    """Обновляет мобильного оператора по ID и возвращает его."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE mobile SET name = $1, shablon = $2 WHERE id = $3 RETURNING id, name, shablon",
            name, shablon, operator_id
        )
        return dict(row) if row else None

async def delete_mobile_operator(operator_id: int):
    """Удаляет мобильного оператора по ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM mobile WHERE id = $1", operator_id)

# --------------------------------------------------------------------------------
# CRUD операции для SIP
# --------------------------------------------------------------------------------

async def add_sip_operator(name: str, shablon: str) -> dict:
    """Добавляет нового SIP оператора и возвращает его."""
    pool = await get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "INSERT INTO sip (name, shablon) VALUES ($1, $2) RETURNING id, name, shablon",
            name, shablon
        )
        return dict(row) if row else None

async def get_all_sip_operators() -> list[dict]:
    """Возвращает список всех SIP операторов."""
    pool = await get_pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch("SELECT id, name, shablon FROM sip ORDER BY id ASC")
        return [dict(row) for row in rows]

async def update_sip_operator(operator_id: int, name: str, shablon: str) -> dict:
    """Обновляет данные SIP оператора и возвращает обновленную запись."""
    pool = await get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "UPDATE sip SET name = $1, shablon = $2 WHERE id = $3 RETURNING id, name, shablon",
            name, shablon, operator_id
        )
        return dict(row) if row else None

async def delete_sip_operator(operator_id: int):
    """Удаляет SIP оператора по ID."""
    pool = await get_pool()
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM sip WHERE id = $1", operator_id)

async def get_gsm_lines_by_gateway_id(gateway_id: int) -> List[Dict]:
    """Получает список GSM-линий для указанного шлюза."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT gl.*, gw.gateway_name as goip_name 
            FROM gsm_lines gl
            JOIN goip gw ON gl.goip_id = gw.id
            WHERE gl.goip_id = $1
            ORDER BY gl.id ASC
            """,
            gateway_id
        )
        return [dict(row) for row in rows]

async def get_gsm_line_by_id(db_id: int) -> Optional[Dict]:
    """Получает одну GSM-линию по ее ПЕРВИЧНОМУ КЛЮЧУ (id)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM gsm_lines WHERE id = $1", db_id)
        return dict(row) if row else None

async def update_gsm_line(line_id: int, line_name: Optional[str], phone_number: Optional[str], prefix: Optional[str]) -> Optional[Dict]:
    """Обновляет информацию о GSM-линии и возвращает обновленную запись."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE gsm_lines 
            SET line_name = $1, phone_number = $2, prefix = $3
            WHERE id = $4
            RETURNING *
            """,
            line_name, phone_number, prefix, line_id
        )
        return dict(row) if row else None

# ============== ФУНКЦИИ ДЛЯ РАБОТЫ С ЗАПИСЯМИ ЗВОНКОВ ==============

async def update_call_recording_info(call_unique_id: str, s3_object_key: str, recording_duration: int) -> bool:
    """
    Обновляет информацию о записи звонка в таблице calls
    ВАЖНО: НЕ ИЗМЕНЯЕТ call_url и uuid_token! Оригинальные значения остаются неизменными!
    
    Args:
        call_unique_id: Уникальный ID звонка
        s3_object_key: Ключ объекта в S3
        recording_duration: Длительность записи в секундах
        
    Returns:
        True при успешном обновлении, False при ошибке
    """
    try:
        async with _pool.acquire() as conn:
            # ИСПРАВЛЕНО: НЕ обновляем call_url И uuid_token! Только S3 данные!
            result = await conn.execute(
                """
                UPDATE calls 
                SET s3_object_key = $1, recording_duration = $2
                WHERE unique_id = $3
                """,
                s3_object_key, recording_duration, call_unique_id
            )
            
            # Проверяем что обновлена хотя бы одна строка
            rows_affected = int(result.split()[-1]) if result else 0
            
            if rows_affected > 0:
                logger.info(f"Обновлена информация о записи для звонка {call_unique_id}")
                return True
            else:
                logger.warning(f"Звонок {call_unique_id} не найден в БД")
                return False
                
    except Exception as e:
        logger.error(f"Ошибка обновления записи звонка {call_unique_id}: {e}")
        return False

async def get_call_recording_info(call_unique_id: str) -> Optional[Dict]:
    """
    Получает информацию о записи звонка по call_unique_id
    
    Args:
        call_unique_id: Уникальный ID звонка
        
    Returns:
        Словарь с информацией о записи или None
    """
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT unique_id, call_url, s3_object_key, uuid_token, recording_duration,
                       enterprise_id, start_time, duration
                FROM calls 
                WHERE unique_id = $1
                """,
                call_unique_id
            )
            
            return dict(row) if row else None
            
    except Exception as e:
        logger.error(f"Ошибка получения информации о записи {call_unique_id}: {e}")
        return None

async def get_call_recording_by_token(uuid_token: str) -> Optional[Dict]:
    """
    Получает информацию о записи звонка по UUID токену
    
    Args:
        uuid_token: UUID токен для поиска записи
        
    Returns:
        Словарь с информацией о записи или None
    """
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT unique_id, call_url, s3_object_key, uuid_token, recording_duration,
                       enterprise_id, start_time, duration, phone_number
                FROM calls 
                WHERE uuid_token = $1
                """,
                uuid_token
            )
            
            return dict(row) if row else None
            
    except Exception as e:
        logger.error(f"Ошибка получения информации о записи по токену {uuid_token}: {e}")
        return None

async def search_calls_with_recordings(enterprise_id: str = None, 
                                     date_from: datetime = None, 
                                     date_to: datetime = None,
                                     limit: int = 100) -> List[Dict]:
    """
    Поиск звонков с записями по критериям
    
    Args:
        enterprise_id: ID предприятия для фильтрации
        date_from: Начальная дата поиска
        date_to: Конечная дата поиска
        limit: Максимальное количество результатов
        
    Returns:
        Список звонков с записями
    """
    try:
        async with _pool.acquire() as conn:
            conditions = ["call_url IS NOT NULL"]
            params = []
            param_count = 0
            
            if enterprise_id:
                param_count += 1
                conditions.append(f"enterprise_id = ${param_count}")
                params.append(enterprise_id)
                
            if date_from:
                param_count += 1
                conditions.append(f"start_time >= ${param_count}")
                params.append(date_from)
                
            if date_to:
                param_count += 1
                conditions.append(f"start_time <= ${param_count}")
                params.append(date_to)
            
            param_count += 1
            params.append(limit)
            
            query = f"""
                SELECT unique_id, call_url, s3_object_key, uuid_token, recording_duration,
                       enterprise_id, start_time, duration, phone_number
                FROM calls 
                WHERE {' AND '.join(conditions)}
                ORDER BY start_time DESC
                LIMIT ${param_count}
            """
            
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
            
    except Exception as e:
        logger.error(f"Ошибка поиска звонков с записями: {e}")
        return [] 