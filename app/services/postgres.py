import asyncpg
from typing import List, Dict, Optional
from datetime import datetime
import logging
import sys
import os

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

async def init_pool():
    """Инициализирует глобальный пул подключений"""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            min_size=2,      # минимальное количество подключений
            max_size=10,     # максимальное количество подключений
            **POSTGRES_CONFIG
        )

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
    print(f"POSTGRES_GET_BY_NUMBER: Вызвана для номера: '{number}' (тип: {type(number)})", file=sys.stderr, flush=True)
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
        print(f"POSTGRES_GET_BY_NUMBER: Выполняется SQL: {sql_query} с параметром: '{number}'", file=sys.stderr, flush=True)
        row = await conn.fetchrow(sql_query, number)
        print(f"POSTGRES_GET_BY_NUMBER: Результат fetchrow: {row} (тип: {type(row)})", file=sys.stderr, flush=True)
        if row:
            print(f"POSTGRES_GET_BY_NUMBER: Предприятие найдено, возвращаем dict(row)", file=sys.stderr, flush=True)
            return dict(row)
        else:
            print(f"POSTGRES_GET_BY_NUMBER: Предприятие НЕ найдено, возвращаем None", file=sys.stderr, flush=True)
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
    print(f"POSTGRES: Начало обновления предприятия {number}")
    print(f"POSTGRES: Параметры: name={name}, ip={ip}, host={host}, name2={name2}")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
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
            print(f"POSTGRES: Выполняем UPDATE для предприятия {number} с запросом: {sql_query} и значениями: {values}")
            result = await conn.execute(sql_query, *values)
            print(f"POSTGRES: Результат UPDATE: {result}")
    except Exception as e:
        print(f"POSTGRES ERROR: {str(e)}")
        raise

async def delete_enterprise(number: str):
    """Удаляет предприятие"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM enterprises WHERE number = $1", number)

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
    """Получает список шлюзов для указанного предприятия."""
    logger.debug(f"POSTGRES_GET_GATEWAYS: Вызвана для enterprise_number: '{enterprise_number}'")
    pool = await get_pool()
    if not pool:
        logger.error("POSTGRES_GET_GATEWAYS ERROR: Пул не инициализирован")
        return []
        
    async with pool.acquire() as conn:
        sql_query = """
            SELECT id, enterprise_number, gateway_name, line_count, 
                   config_backup_original_name, config_backup_uploaded_at,
                   custom_boolean_flag
            FROM goip
            WHERE enterprise_number = $1
            ORDER BY id ASC
        """
        try:
            rows = await conn.fetch(sql_query, enterprise_number)
            logger.debug(f"POSTGRES_GET_GATEWAYS: Найдено {len(rows)} шлюзов для предприятия {enterprise_number}")
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"POSTGRES_GET_GATEWAYS ERROR: Ошибка при выполнении запроса для {enterprise_number}: {e}")
            return []

async def get_goip_gateway_by_id(gateway_id: int) -> Optional[Dict]:
    """Получает шлюз по его ID."""
    logger.debug(f"POSTGRES_GET_GATEWAY_BY_ID: Вызвана для ID: {gateway_id}")
    pool = await get_pool()
    if not pool:
        logger.error("POSTGRES_GET_GATEWAY_BY_ID ERROR: Пул не инициализирован")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM goip WHERE id = $1", gateway_id)
        if row:
            return dict(row)
        return None

async def _get_max_line_id(conn) -> int:
    """Вспомогательная функция для получения максимального line_id."""
    max_id = await conn.fetchval("SELECT MAX(line_id) FROM gsm_lines")
    return max_id if max_id is not None else 1362 # Возвращаем 1362, чтобы первая линия была 1363

async def _get_last_internal_id_details(conn, enterprise_number: str) -> tuple[int, int]:
    """
    Получает детали последнего internal_id для предприятия.
    Возвращает кортеж (номер_блока, номер_линии_в_блоке).
    """
    last_internal_id = await conn.fetchval(
        "SELECT internal_id FROM gsm_lines WHERE enterprise_number = $1 ORDER BY internal_id DESC LIMIT 1",
        enterprise_number
    )
    if not last_internal_id:
        return 10, 0 # Если линий нет, начинаем с 10-го блока, 0-й линии

    # Парсим ID: первые 2 символа - блок, остальное - номер предприятия и линии
    block_part_str = last_internal_id[:2]
    rest_part_str = last_internal_id[2:]
    
    # Из оставшейся части убираем номер предприятия, чтобы получить номер линии
    line_in_block_str = rest_part_str.replace(enterprise_number, '', 1)

    try:
        block_part = int(block_part_str)
        line_in_block = int(line_in_block_str)
        return block_part, line_in_block
    except (ValueError, TypeError):
        # В случае ошибки парсинга, возвращаем значения по умолчанию
        return 10, 0

async def _add_gsm_line(conn, goip_id, enterprise_number, line_id, internal_id, prefix, goip_name):
    """Добавляет одну запись в gsm_lines, включая имя шлюза."""
    await conn.execute(
        """
        INSERT INTO gsm_lines (goip_id, enterprise_number, line_id, internal_id, prefix, goip_name)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        goip_id, enterprise_number, line_id, internal_id, prefix, goip_name
    )

async def create_gsm_lines_for_gateway(gateway_id: int, gateway_name: str, enterprise_number: str, line_count: int):
    """Создает все GSM-линии для нового шлюза."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Получаем начальные значения для ID
            next_line_id = await _get_max_line_id(conn) + 1
            block, last_line_in_block = await _get_last_internal_id_details(conn, enterprise_number)

            for i in range(line_count):
                # 1. Генерируем line_id и форматируем его
                current_line_id = str(next_line_id + i).zfill(7)

                # 2. Генерируем internal_id
                last_line_in_block += 1
                if last_line_in_block > 99:
                    block += 1
                    last_line_in_block = 1
                
                line_in_block_str = str(last_line_in_block).zfill(2)
                current_internal_id = f"{block}{enterprise_number}{line_in_block_str}"

                # 3. Генерируем prefix
                current_prefix = 21 + i

                # 4. Добавляем линию в БД
                await _add_gsm_line(
                    conn,
                    gateway_id,
                    enterprise_number,
                    current_line_id,
                    current_internal_id,
                    str(current_prefix),
                    gateway_name
                )

async def add_goip_gateway(enterprise_number: str, gateway_name: str, line_count: Optional[int],
                           config_backup_filename: Optional[str] = None, 
                           config_backup_original_name: Optional[str] = None,
                           config_backup_uploaded_at: Optional[datetime] = None,
                           custom_boolean_flag: Optional[bool] = False) -> Dict:
    """
    Добавляет новый шлюз в таблицу 'goip' и возвращает созданную запись как словарь.
    Создание gsm_lines временно отключено.
    """
    logger.debug(f"POSTGRES_ADD_GOIP_GATEWAY: Добавление шлюза для предприятия {enterprise_number}, имя: {gateway_name}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql_query = """
            INSERT INTO goip (enterprise_number, gateway_name, line_count, 
                              config_backup_filename, config_backup_original_name, config_backup_uploaded_at,
                              created_at, custom_boolean_flag)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
        """
        try:
            # Используем fetchrow для получения всей созданной записи
            new_gateway_record = await conn.fetchrow(
                sql_query, enterprise_number, gateway_name, line_count,
                config_backup_filename, config_backup_original_name, config_backup_uploaded_at,
                datetime.utcnow(), custom_boolean_flag
            )
            logger.info(f"POSTGRES_ADD_GOIP_GATEWAY: Шлюз успешно добавлен с ID: {new_gateway_record['id']} для предприятия {enterprise_number}")
            
            # Вызов для создания GSM-линий временно отключен по требованию
            # if line_count and line_count > 0:
            #     await create_gsm_lines_for_gateway(conn, new_gateway_record['id'], enterprise_number, line_count)

            return dict(new_gateway_record)
        except Exception as e:
            logger.error(f"POSTGRES_ADD_GOIP_GATEWAY ERROR: Ошибка при добавлении шлюза для {enterprise_number}: {e}")
            raise

async def update_goip_gateway(gateway_id: int, gateway_name: Optional[str] = None, line_count: Optional[int] = None,
                              config_backup_filename: Optional[str] = None,
                              config_backup_original_name: Optional[str] = None,
                              config_backup_uploaded_at: Optional[datetime] = None,
                              custom_boolean_flag: Optional[bool] = None):
    """Обновляет информацию о существующем шлюзе. Обновляет только переданные поля."""
    logger.debug(f"POSTGRES_UPDATE_GOIP_GATEWAY: Обновление шлюза ID: {gateway_id}")
    pool = await get_pool()
    
    fields_to_update = {}
    if gateway_name is not None:
        fields_to_update["gateway_name"] = gateway_name
    if line_count is not None:
        # Эта проверка не даст случайно обновить кол-во линий, если не передано явно
        fields_to_update["line_count"] = line_count
    if config_backup_filename is not None:
        fields_to_update["config_backup_filename"] = config_backup_filename
    if config_backup_original_name is not None:
        fields_to_update["config_backup_original_name"] = config_backup_original_name
    if config_backup_uploaded_at is not None:
        fields_to_update["config_backup_uploaded_at"] = config_backup_uploaded_at
    if custom_boolean_flag is not None:
        fields_to_update["custom_boolean_flag"] = custom_boolean_flag
    
    if not fields_to_update:
        logger.debug(f"POSTGRES_UPDATE_GOIP_GATEWAY: Нет полей для обновления шлюза ID: {gateway_id}")
        return

    set_clauses = []
    values = []
    param_idx = 1
    for key, value in fields_to_update.items():
        set_clauses.append(f"{key} = ${param_idx}")
        values.append(value)
        param_idx += 1
    
    values.append(gateway_id)
    
    sql_query = f"""
        UPDATE goip
        SET {", ".join(set_clauses)}
        WHERE id = ${param_idx}
    """
    async with pool.acquire() as conn:
        try:
            await conn.execute(sql_query, *values)
            logger.info(f"POSTGRES_UPDATE_GOIP_GATEWAY: Шлюз ID: {gateway_id} успешно обновлен.")
        except Exception as e:
            logger.error(f"POSTGRES_UPDATE_GOIP_GATEWAY ERROR: Ошибка при обновлении шлюза ID: {gateway_id}: {e}")
            raise

async def delete_goip_gateway(gateway_id: int):
    """Удаляет шлюз по ID."""
    logger.debug(f"POSTGRES_DELETE_GOIP_GATEWAY: Удаление шлюза ID: {gateway_id}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await conn.execute("DELETE FROM goip WHERE id = $1", gateway_id)
            # result будет содержать, например, 'DELETE 1', если одна строка была удалена
            if result == "DELETE 1":
                 logger.info(f"POSTGRES_DELETE_GOIP_GATEWAY: Шлюз ID: {gateway_id} успешно удален.")
            else:
                 logger.warning(f"POSTGRES_DELETE_GOIP_GATEWAY: Шлюз ID: {gateway_id} не найден для удаления или удалено 0 строк.")
        except Exception as e:
            logger.error(f"POSTGRES_DELETE_GOIP_GATEWAY ERROR: Ошибка при удалении шлюза ID: {gateway_id}: {e}")
            raise

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

# Далее существующий код...
# Например, если следующая функция это:
# async def some_other_function():
#    pass
# Убедитесь, что новые функции вставлены корректно относительно других. 