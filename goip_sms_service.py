"""
Сервис GoIP SMS Receiver (goip_sms_service.py)

Предназначение:
Этот сервис написан на FastAPI и предназначен для приема входящих SMS-сообщений от GoIP-шлюзов.
Он сохраняет полученные SMS в базу данных PostgreSQL и, при определенных условиях,
пересылает их в Telegram-бот, связанный с конкретным предприятием.

Основные компоненты и логика работы:

1.  Инициализация и зависимости:
    - Использует FastAPI для создания веб-сервера и обработки HTTP-запросов.
    - Подключается к базе данных PostgreSQL с использованием библиотеки `asyncpg`.
      Функции для работы с пулом соединений (`init_pool`, `close_pool`, `get_pool`)
      и специфические запросы к БД (например, `get_enterprise_by_name2_suffix`)
      импортируются из `app.services.postgres`.
    - Импортирует функцию `send_message_to_bot` из `app.services.enterprise` для отправки
      сообщений в Telegram. Если импорт не удается, используется заглушка.

2.  Модели данных (Pydantic):
    - `GoIPIncomingSms`: Определяет структуру JSON-объекта, который GoIP-шлюз отправляет
      на вебхук сервиса. Включает поля:
        - `goip_line`: Идентификатор линии/порта GoIP-шлюза, с которого пришло SMS.
        - `from_number`: Номер телефона отправителя SMS.
        - `content`: Текст SMS-сообщения.
        - `recv_time`: Время получения SMS GoIP-шлюзом (в формате "ГГГГ-ММ-ДД чч:мм:сс").
    - `StoredSms`: Расширяет `GoIPIncomingSms`, добавляя поле:
        - `received_at`: Время получения SMS данным сервисом (с временной зоной).
          Используется для ответа API на GET-запрос.

3.  Управление пулом соединений PostgreSQL:
    - `startup_event`: При запуске FastAPI-приложения инициализирует пул соединений с БД.
    - `shutdown_event`: При остановке приложения корректно закрывает пул соединений.

4.  Основной эндпоинт - Вебхук для приема SMS:
    - `POST /webhook/goip/incoming_sms`:
        - Принимает POST-запрос с JSON-телом, соответствующим модели `GoIPIncomingSms`.
        - Логирует факт получения SMS и его основные данные.
        - Преобразует строку `recv_time` от GoIP в объект `datetime`.
        - Сохраняет SMS в таблицу `goip_incoming_sms` базы данных PostgreSQL.
          При сохранении используется SQL-запрос:
          `INSERT INTO goip_incoming_sms (goip_line, from_number, content, goip_recv_time) VALUES (...) RETURNING id`
          для получения `id` вставленной записи.
        - Если SMS успешно сохранено и получен его `id`, запускает фоновую задачу
          `process_sms_for_bot` для дальнейшей обработки.
        - Возвращает JSON-ответ о статусе операции.

5.  Эндпоинт для получения сохраненных SMS:
    - `GET /api/goip/received_sms`:
        - Позволяет получить список последних SMS, сохраненных в базе данных.
        - Принимает необязательный параметр `limit` (по умолчанию 50, максимум 500)
          для ограничения количества возвращаемых записей.
        - Выполняет SQL-запрос:
          `SELECT goip_line, from_number, content, goip_recv_time, service_recv_time FROM goip_incoming_sms ORDER BY id DESC LIMIT $1`
        - Преобразует записи из БД в список объектов `StoredSms` и возвращает его.

6.  Фоновая задача обработки SMS для бота (`process_sms_for_bot`):
    - Эта асинхронная функция выполняется в фоне (`BackgroundTasks`) после сохранения SMS,
      чтобы не задерживать ответ GoIP-шлюзу.
    - Принимает `sms_id` (ID сохраненного SMS в БД), `goip_line`, `from_number`, `sms_content`.
    - Основная логика:
        - **Условие:** Если `goip_line` равен "10062713":
            1.  Вызывается функция `get_enterprise_by_name2_suffix("0627")` из
                `app.services.postgres`. Эта функция ищет в таблице `enterprises`
                запись, у которой поле `name2` заканчивается на "0627" (SQL: `WHERE name2 LIKE '%0627'`).
            2.  Если предприятие найдено и у него есть `bot_token` и `chat_id`:
                - Формируется сообщение для Telegram-бота, включающее линию GoIP, номер отправителя и текст SMS.
                - Вызывается функция `send_message_to_bot(bot_token, chat_id, message_to_bot)`.
                - Статус отправки (успех/ошибка) логируется.
                - Вызывается функция `update_sms_bot_status` для обновления записи SMS в БД.
            3.  Если предприятие найдено, но данных для бота нет (токен/chat_id отсутствуют),
                статус обновляется как "ERROR_ENTERPRISE_DATA_INCOMPLETE".
            4.  Если предприятие по суффиксу "0627" не найдено, статус обновляется как
                "INFO_ENTERPRISE_NOT_FOUND_FOR_SUFFIX".
        - Если `goip_line` не "10062713", SMS не предназначено для специальной обработки,
          и это логируется. Можно также обновить статус на "NOT_APPLICABLE_LINE".

7.  Обновление статуса обработки SMS в БД (`update_sms_bot_status`):
    - Принимает `sms_id`, `status` (строка, например, "SUCCESS", "ERROR_SEND_MESSAGE_FALSE")
      и `processed` (булево значение).
    - Выполняет SQL-запрос:
      `UPDATE goip_incoming_sms SET processed_by_bot = $1, bot_send_status = $2, bot_send_attempt_time = $3 WHERE id = $4`
      Записывает, была ли предпринята попытка обработки ботом, ее результат и время попытки.

8.  Структура таблицы `goip_incoming_sms` в PostgreSQL (с которой взаимодействует сервис):
    - `id SERIAL PRIMARY KEY`: Уникальный идентификатор записи SMS.
    - `goip_line VARCHAR(255) NOT NULL`: ID линии GoIP, с которой пришло SMS (из `sms.goip_line`).
    - `from_number VARCHAR(255) NOT NULL`: Номер отправителя SMS (из `sms.from_number`).
    - `content TEXT`: Текст SMS-сообщения (из `sms.content`).
    - `goip_recv_time TIMESTAMP WITHOUT TIME ZONE NOT NULL`: Время получения SMS шлюзом GoIP
      (из `sms.recv_time`, преобразованное в timestamp).
    - `service_recv_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`: Время получения SMS
      данным сервисом (устанавливается автоматически при вставке).
    - `processed_by_bot BOOLEAN DEFAULT FALSE`: Флаг, указывающий, была ли произведена
      попытка обработки этого SMS для отправки боту (True/False). Управляется функцией `update_sms_bot_status`.
    - `bot_send_attempt_time TIMESTAMP WITH TIME ZONE`: Время последней попытки отправки SMS боту.
      Управляется функцией `update_sms_bot_status`.
    - `bot_send_status VARCHAR(255)`: Строковый код статуса последней попытки отправки
      (например, "SUCCESS", "ERROR_SEND_EXCEPTION", "INFO_ENTERPRISE_NOT_FOUND_FOR_SUFFIX").
      Управляется функцией `update_sms_bot_status`.

Запуск сервиса:
- Сервис может быть запущен напрямую (`python goip_sms_service.py`) для локальной отладки,
  в этом случае Uvicorn запускается на `http://0.0.0.0:8002`.
- Для production или управляемого запуска используется скрипт `sms.sh`, который также
  запускает Uvicorn, но с возможностью управления (start, stop, restart, status).
"""
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
import uvicorn
import sys # Для вывода в stderr
import asyncpg # Для работы с PostgreSQL и явного указания asyncpg.PostgresError

# Попытаемся импортировать функции для работы с PostgreSQL из существующего модуля
try:
    from app.services.postgres import init_pool, close_pool, get_pool, POSTGRES_CONFIG, get_enterprise_by_name2_suffix
except ImportError as e_postgres:
    print(f"ERROR: Не удалось импортировать app.services.postgres: {e_postgres}", file=sys.stderr)
    sys.exit("Критическая ошибка: не найден модуль app.services.postgres или его компоненты")

# Попытаемся импортировать функцию отправки сообщения боту
try:
    from app.services.enterprise import send_message_to_bot
except ImportError as e_enterprise:
    print(f"ERROR: Не удалось импортировать send_message_to_bot из app.services.enterprise: {e_enterprise}", file=sys.stderr)
    # Если функция критична, можно также сделать sys.exit()
    # Пока что просто выведем ошибку и продолжим, но отправка боту не будет работать.
    async def send_message_to_bot(token: str, chat_id: str, message: str):
        print(f"ЗАГЛУШКА send_message_to_bot: token={token}, chat_id={chat_id}, message='{message}'", file=sys.stderr)
        print("ERROR: Реальная функция send_message_to_bot не найдена!", file=sys.stderr)
        return False # Имитируем неудачную отправку

# --- Модели данных ---
class GoIPIncomingSms(BaseModel):
    goip_line: str
    from_number: str
    content: str
    recv_time: str # Формат "ГГГГ-ММ-ДД чч:мм:сс"

class StoredSms(GoIPIncomingSms):
    received_at: datetime # Время получения нашим сервисом

# --- Хранилище SMS (в памяти) --- БОЛЬШЕ НЕ ИСПОЛЬЗУЕТСЯ
# # В реальном приложении здесь была бы база данных
# sms_storage: List[StoredSms] = []
# MAX_STORED_SMS = 200 # Хранить не более N последних SMS

# --- Приложение FastAPI ---
app = FastAPI(
    title="GoIP SMS Receiver Service",
    description="Сервис для приема и хранения SMS, пересылаемых с GoIP сервера.",
    version="0.1.0"
)

@app.on_event("startup")
async def startup_event():
    print("GoIP Service: Инициализация пула соединений PostgreSQL...", file=sys.stderr, flush=True)
    await init_pool()
    print("GoIP Service: Пул соединений PostgreSQL инициализирован.", file=sys.stderr, flush=True)

@app.on_event("shutdown")
async def shutdown_event():
    print("GoIP Service: Закрытие пула соединений PostgreSQL...", file=sys.stderr, flush=True)
    await close_pool()
    print("GoIP Service: Пул соединений PostgreSQL закрыт.", file=sys.stderr, flush=True)

# --- Эндпоинты ---
@app.post("/webhook/goip/incoming_sms")
async def webhook_goip_incoming_sms(sms: GoIPIncomingSms, request: Request, background_tasks: BackgroundTasks):
    """
    Вебхук для приема входящих SMS от GoIP сервера.
    GoIP должен быть настроен на отправку POST-запросов сюда.
    Сохраняет SMS и запускает фоновую задачу для отправки боту.
    """
    print(f"GoIP Service: Получено SMS от GoIP: {sms.from_number} на линию {sms.goip_line} в {sms.recv_time}", file=sys.stderr, flush=True)
    print(f"GoIP Service: Содержимое: {sms.content}", file=sys.stderr, flush=True)
    
    pool = await get_pool()
    if not pool:
        print("GoIP Service ERROR: Пул соединений PostgreSQL не инициализирован.", file=sys.stderr, flush=True)
        raise HTTPException(status_code=500, detail="Ошибка конфигурации сервера: пул БД недоступен")

    try:
        # Преобразование строки времени от GoIP в datetime объект
        # Формат от GoIP: "ГГГГ-ММ-ДД чч:мм:сс"
        goip_recv_dt = datetime.strptime(sms.recv_time, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        print(f"GoIP Service ERROR: Неверный формат времени recv_time: {sms.recv_time}. Ошибка: {e}", file=sys.stderr, flush=True)
        # Можно вернуть ошибку клиенту или сохранить с NULL/умолчанием, если поле позволяет
        raise HTTPException(status_code=400, detail=f"Неверный формат времени recv_time: {sms.recv_time}")

    async with pool.acquire() as conn:
        try:
            # Добавляем RETURNING id, чтобы получить ID вставленной записи
            inserted_id = await conn.fetchval("""
                INSERT INTO goip_incoming_sms (goip_line, from_number, content, goip_recv_time)
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """, sms.goip_line, sms.from_number, sms.content, goip_recv_dt)
            print(f"GoIP Service: SMS от {sms.from_number} сохранено в БД с ID: {inserted_id}.", file=sys.stderr, flush=True)
            
            # Если SMS успешно сохранено, добавляем задачу на его обработку ботом
            if inserted_id:
                background_tasks.add_task(process_sms_for_bot, inserted_id, sms.goip_line, sms.from_number, sms.content)
            else:
                print(f"GoIP Service WARNING: Не удалось получить ID для сохраненного SMS от {sms.from_number}. Обработка ботом не будет запущена.", file=sys.stderr, flush=True)

        except asyncpg.PostgresError as e:
            print(f"GoIP Service ERROR: Ошибка при сохранении SMS в БД: {e}", file=sys.stderr, flush=True)
            # В зависимости от политики, можно попытаться повторить или просто вернуть ошибку
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при сохранении SMS: {e}")
        except Exception as e:
            print(f"GoIP Service ERROR: Непредвиденная ошибка при работе с БД: {e}", file=sys.stderr, flush=True)
            raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")
            
    return {"status": "success", "message": "SMS received and stored in DB"}

@app.get("/api/goip/received_sms", response_model=List[StoredSms])
async def get_received_sms(limit: int = 50):
    """
    Получить список последних полученных SMS из базы данных.
    """
    if limit <= 0:
        limit = 50
    if limit > 500: # Ограничим максимальный limit во избежание слишком больших запросов
        limit = 500

    pool = await get_pool()
    if not pool:
        print("GoIP Service ERROR: Пул соединений PostgreSQL не инициализирован.", file=sys.stderr, flush=True)
        raise HTTPException(status_code=500, detail="Ошибка конфигурации сервера: пул БД недоступен")

    results: List[StoredSms] = []
    async with pool.acquire() as conn:
        try:
            # Выбираем поля, необходимые для модели StoredSms, и сортируем по ID в обратном порядке
            # service_recv_time также хороший кандидат для сортировки
            db_records = await conn.fetch("""
                SELECT goip_line, from_number, content, goip_recv_time, service_recv_time
                FROM goip_incoming_sms
                ORDER BY id DESC 
                LIMIT $1
            """, limit)
            
            for record in db_records:
                # Преобразуем goip_recv_time (datetime из БД) в строку нужного формата
                # service_recv_time из БД уже datetime, что соответствует полю received_at в StoredSms
                results.append(
                    StoredSms(
                        goip_line=record['goip_line'],
                        from_number=record['from_number'],
                        content=record['content'],
                        recv_time=record['goip_recv_time'].strftime("%Y-%m-%d %H:%M:%S"),
                        received_at=record['service_recv_time']
                    )
                )
            print(f"GoIP Service: Извлечено {len(results)} SMS из БД.", file=sys.stderr, flush=True)
        except asyncpg.PostgresError as e:
            print(f"GoIP Service ERROR: Ошибка при извлечении SMS из БД: {e}", file=sys.stderr, flush=True)
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при извлечении SMS: {e}")
        except Exception as e:
            print(f"GoIP Service ERROR: Непредвиденная ошибка при работе с БД: {e}", file=sys.stderr, flush=True)
            raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")
            
    return results

@app.get("/")
async def root():
    return {"message": "GoIP SMS Receiver Service is running. Используйте /docs для API."}

# --- Запуск сервера (для локальной отладки) ---
# Этот блок будет использоваться, если скрипт запускается напрямую: python goip_sms_service.py
# Для запуска через uvicorn из sms.sh, этот блок не так важен, но полезен для быстрой проверки.
if __name__ == "__main__":
    print("Запуск GoIP SMS Receiver Service на http://0.0.0.0:8002", file=sys.stderr, flush=True)
    # Uvicorn рекомендуется запускать через командную строку для production/управляемого запуска,
    # но для простоты оставим так для прямого запуска скрипта.
    # Для sms.sh будем использовать 'uvicorn goip_sms_service:app ...'
    uvicorn.run(app, host="0.0.0.0", port=8002) 

# Помещаем определение функции process_sms_for_bot перед ее первым использованием
async def update_sms_bot_status(sms_id: int, status: str, processed: bool):
    """Обновляет статус обработки SMS ботом в БД."""
    pool = await get_pool()
    if not pool: return

    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                UPDATE goip_incoming_sms
                SET processed_by_bot = $1, bot_send_status = $2, bot_send_attempt_time = $3
                WHERE id = $4
            """, processed, status, datetime.now(timezone.utc), sms_id)
            print(f"GoIP Service: Статус обработки ботом для SMS ID {sms_id} обновлен: processed={processed}, status='{status}'", file=sys.stderr, flush=True)
        except asyncpg.PostgresError as e:
            print(f"GoIP Service ERROR: Ошибка при обновлении статуса SMS ID {sms_id} в БД: {e}", file=sys.stderr, flush=True)

async def process_sms_for_bot(sms_id: int, goip_line: str, from_number: str, sms_content: str):
    """
    Обрабатывает SMS для отправки боту в соответствии с условиями.
    Извлекает суффикс из goip_line (отбрасывая первые 2 и последние 2 символа)
    и ищет предприятие по этому суффиксу в поле name2.
    """
    print(f"GoIP Service (BG Task): Обработка SMS ID {sms_id} от {from_number} с линии {goip_line}", file=sys.stderr, flush=True)

    # Извлекаем суффикс из goip_line
    # Убедимся, что goip_line достаточно длинная, чтобы избежать ошибок
    if len(goip_line) > 4: # Должно быть как минимум 5 символов, чтобы что-то осталось после отбрасывания 4-х
        name2_suffix = goip_line[2:-2]
        print(f"GoIP Service (BG Task): Извлечен суффикс '{name2_suffix}' из линии {goip_line} для SMS ID {sms_id}.", file=sys.stderr, flush=True)

        if not name2_suffix: # Если после срезания ничего не осталось (например, goip_line была "1002")
            print(f"GoIP Service (BG Task) WARNING: Получен пустой суффикс из goip_line '{goip_line}' для SMS ID {sms_id}. Отправка боту невозможна.", file=sys.stderr, flush=True)
            await update_sms_bot_status(sms_id, "ERROR_EMPTY_SUFFIX_FROM_LINE", False)
            return

        enterprise = await get_enterprise_by_name2_suffix(name2_suffix)
        
        if enterprise and enterprise.get("bot_token") and enterprise.get("chat_id"):
            bot_token = enterprise["bot_token"]
            chat_id = enterprise["chat_id"]
            ent_number = enterprise.get("number", "N/A")
            ent_name2 = enterprise.get("name2", "N/A") # Добавим для логирования
            print(f"GoIP Service (BG Task): Найдено предприятие {ent_number} (name2: {ent_name2}) для суффикса '{name2_suffix}'. Попытка отправки SMS.", file=sys.stderr, flush=True)
            
            message_to_bot = f"Входящее SMS с линии {goip_line} от {from_number}:\n\n{sms_content}"
            
            try:
                success = await send_message_to_bot(bot_token, chat_id, message_to_bot)
                if success:
                    print(f"GoIP Service (BG Task): SMS ID {sms_id} успешно отправлено боту предприятия {ent_number}.", file=sys.stderr, flush=True)
                    await update_sms_bot_status(sms_id, "SUCCESS", True)
                else:
                    print(f"GoIP Service (BG Task) ERROR: Функция send_message_to_bot вернула False для SMS ID {sms_id} (предприятие {ent_number}).", file=sys.stderr, flush=True)
                    await update_sms_bot_status(sms_id, "ERROR_SEND_MESSAGE_FALSE", False)
            except Exception as e_send:
                print(f"GoIP Service (BG Task) ERROR: Исключение при отправке SMS ID {sms_id} боту: {e_send}", file=sys.stderr, flush=True)
                await update_sms_bot_status(sms_id, f"ERROR_SEND_EXCEPTION: {type(e_send).__name__}", False)
        elif enterprise:
            # Логируем, если предприятие найдено, но данных для бота не хватает
            ent_number_partial = enterprise.get("number", "N/A")
            print(f"GoIP Service (BG Task) WARNING: Найдено предприятие {ent_number_partial} для суффикса '{name2_suffix}', но отсутствует bot_token или chat_id. SMS ID {sms_id}", file=sys.stderr, flush=True)
            await update_sms_bot_status(sms_id, f"ERROR_ENTERPRISE_DATA_INCOMPLETE_FOR_SUFFIX_{name2_suffix}", False)
        else:
            print(f"GoIP Service (BG Task) INFO: Предприятие для суффикса '{name2_suffix}' не найдено. SMS ID {sms_id} не будет отправлено боту.", file=sys.stderr, flush=True)
            # Обновляем статус, указывая, что предприятие не найдено для конкретного суффикса
            await update_sms_bot_status(sms_id, f"INFO_ENTERPRISE_NOT_FOUND_FOR_SUFFIX_{name2_suffix}", False)
    else:
        # Если goip_line слишком короткая для извлечения суффикса
        print(f"GoIP Service (BG Task) WARNING: Длина goip_line '{goip_line}' (SMS ID {sms_id}) недостаточна для извлечения суффикса (нужно > 4 символов). Отправка боту невозможна.", file=sys.stderr, flush=True)
        await update_sms_bot_status(sms_id, "ERROR_LINE_TOO_SHORT_FOR_SUFFIX", False) 