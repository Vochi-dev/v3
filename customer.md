# Customers: модель клиента и трекер внедрения

## Цель
Единая сущность клиента (по телефону) с быстрыми агрегатами для Telegram/аналитики и подготовкой к mini‑CRM.

## Базовая схема (таблица `customers`)
Ключ: `(enterprise_number, phone_e164)` — телефон строго в E.164.

Поля:
- `id` bigint PK (serial/identity)
- `enterprise_number` text NOT NULL
- `phone_e164` text NOT NULL
- `first_seen_at` timestamptz
- `last_seen_at` timestamptz
- Счётчики и статус:
  - `calls_total_in` int DEFAULT 0
  - `calls_total_out` int DEFAULT 0
  - `calls_answered_in` int DEFAULT 0
  - `calls_answered_out` int DEFAULT 0
  - `calls_missed_in` int DEFAULT 0
  - `calls_missed_out` int DEFAULT 0
  - `last_call_at` timestamptz
  - `last_call_direction` text CHECK in ('in','out','internal','unknown')
  - `last_call_status` text CHECK in ('answered','missed','unknown')
  - `last_success_at` timestamptz NULL
  - `last_agent_internal` text NULL      # кто последний говорил (внутренний код)
  - `last_line` text NULL                # по какой внешней линии
- Mini‑CRM поля (пользовательские):
  - `field1` varchar(100) NULL
  - `field2` varchar(100) NULL
  - `field3` varchar(100) NULL
  - `field4` varchar(100) NULL
- Управление:
  - `black_list` boolean DEFAULT false   # правила заполнения определим отдельно
  - `meta` jsonb NULL                    # расширяемое место (теги, источники)
- ФИО и организация:
  - `last_name` varchar(100) NULL
  - `first_name` varchar(100) NULL
  - `middle_name` varchar(100) NULL
  - `enterprise_name` varchar(200) NULL

Ограничения/индексы:
- UNIQUE (enterprise_number, phone_e164)
- btree (enterprise_number, last_call_at DESC)

## Поток обновления (live и recovery)
Источник истины — событие `hangup` (знаем исход и длительность). Обновляем строго при `hangup`:
1) Нормализуем номер (уже есть).
2) UPSERT в `customers` по `(enterprise_number, phone_e164)`:
   - если нет — INSERT с `first_seen_at = now()`;
   - всегда: `last_seen_at = now()`, `last_call_at`, `last_call_direction`, `last_call_status`, `last_line`, `last_agent_internal`;
   - инкремент соответствующих счётчиков `*_in/*_out` и `answered/missed`;
   - если успешный звонок — `last_success_at = now()`.
3) Важно для Telegram «до этого»: перед инкрементом делаем SELECT текущих счётчиков; формируем текст и только затем выполняем UPDATE/UPSERT.
4) Источник события:
   - live (8000): обновляет `customers` сразу в обработке `hangup`;
   - recovery (8007): выполняет ту же логику UPSERT для восстановленных записей. Должен использовать нормализованный номер и корректные времена начала/окончания (берутся из восстановленного события).

## Касание сервисов
- 8000 (start/dial/bridge/hangup):
  - при `hangup` — чтение «старых» счётчиков → формирование Telegram → UPSERT агрегатов.
- 8007 (download/recovery):
  - при дозаливке/восстановлении событий `hangup` — выполнять тот же UPSERT (идемпотентно по `(enterprise, phone, start/end)` либо по уникальному `UniqueId` события).
- 8020: НЕ обязателен. Опционально добавить кэш «customer-facts» (TTL 60–120с) для разгрузки БД при множественных уведомлениях.

## Политика нормализации номера
- На входе всех сервисов (уже реализовано): использует правила `incoming_transform` для линий; в таблицу `customers` всегда пишем E.164.

## Начальная стратегия заполнения (backfill)
- Одноразовый job: пройти по `calls` за период → для каждой записи выполнить логическую агрегацию, заполнить `customers`.
- Конфликты решаем по `last_call_at`/`last_success_at` (берём максимумы).

## Тест-кейсы
- IN: серия входящих по одному номеру с пропусками/ответами → счётчики и `last_*` корректны.
- OUT: исходящие к тому же номеру → out‑счётчики инкрементируются отдельно.
- Mixed: входящий → исходящий → входящий.
- Recovery: восстановленные `hangup` корректно пересчитывают агрегаты (идемпотентность).
- Blacklist: поле не меняется автоматически; переключение не ломает агрегаты.
- Поля field1..field4: CRUD в админке mini‑CRM (в последующих задачах).

## To‑Do
1) DDL: создать таблицу `customers` с полями, индексами, чеками. [x]
2) 8000/hangup: SELECT фактов «до этого» + UPSERT агрегатов. [x]
3) 8007 (recovery): тот же UPSERT на восстановленных `hangup`. [x]
4) (Опц.) 8020: GET `/customer-facts/{enterprise}/{phone}` (TTL) для быстрых уведомлений. [ ]
5) Backfill из `calls` (скрипт одного запуска). [ ]
6) Инструменты мониторинга: простая выборка «топ клиентов за N дней» (SQL), проверка расхождений с `calls`. [ ]
7) Документация: правила нормализации, когда читаем «до этого», форматы статусов. [ ]
8) Обогащение профиля (live, 8000): на `hangup` отправлять fire‑and‑forget запрос в 8020 за профилем (ФИО/enterprise_name). По готовности — обновлять `customers` и, при наличии message_id, делать edit сообщения в Телеграм (добавлять «Фамилия Имя (Компания)»). [ ]
9) 8020 (интеграционный слой): реализовать lookup профиля в «главной» интеграции с single‑flight по ключу `(enterprise, phone)` и коротким negative‑TTL (2–5 мин). Долгоживущего кэша не держать; «источник истины» для профиля — таблица `customers`. [ ]
10) Обогащение профиля (recovery, 8007/download): по окончании обработки файла/предприятия формировать список уникальных номеров и батчем запрашивать 8020 (с RPS‑лимитом). Обновлять `customers`. Телеграм при recovery использует данные из `customers` без онлайн‑запросов. [ ]
11) Политика кэширования: не хранить «массив последних звонков». Все факты по звонкам читаем из БД; долговременный кэш профиля — только `customers`. На 8000 допускается короткий локальный single‑flight LRU для дедупликации в момент шквала. [ ]
12) Наблюдаемость/устойчивость: метрики hit/miss 8020, лимиты на внешнее API, backoff + circuit‑breaker, логи ошибок обогащения; ретраи фонового обновления `customers`. [ ]

## Примечания
- Все даты/времена — UTC.
- Согласованность: обновление `customers` должно быть частью одной транзакции с записью `calls`/финализацией события `hangup` (если возможно), либо с ретраями при сбое.
- В будущем можно расширить: таблица заметок по клиенту, связи с внешними CRM, связи «несколько телефонов → один клиент`.
