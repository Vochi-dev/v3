# Smart Redirect: анализ на хосте 0367 (10.88.10.19)

## Файлы и расположение
- Основные файлы Asterisk на хосте: `/etc/asterisk/extensions.conf`, `/etc/asterisk/extensions_custom.conf`.
- Сгенерированные контексты Smart Redirect находятся в `/etc/asterisk/extensions.conf` между маркерами:
  `;******************************Smart Redirection******************************************`.

## Крючок Smart Redirect в шапке from-out-office
В `/etc/asterisk/extensions.conf` в контексте `[from-out-office]` присутствуют строки:
- `same => n,Set(INCALL=${EXTEN})`
- `same => n,Macro(getcustomerdata,${CALLERID(num)},${EXTEN})`
- `same => n,GotoIf($["${NEXT}" != ""]?${NEXT},${INCALL},1)`

Смысл:
- Выставляется `INCALL` (внешняя линия).
- Вызывается макрос `getcustomerdata`, который делает HTTP-запрос на сервер и выставляет переменные `CALLNAME` (отображаемое имя) и `NEXT` (целевой контекст).
- Если `NEXT` непустой, выполняется `Goto(${NEXT},${INCALL},1)` — звонок уходит в указанный Smart Redirect контекст.

## Макрос getcustomerdata (старая реализация на хосте)
Объявление — в `/etc/asterisk/extensions_custom.conf` (`[macro-getcustomerdata]`). Ключевые шаги:
- Формирует JSON POST:
  - `{"UniqueId":"${UNIQUEID}","Phone":"${ARG1}","TrunkId":"${ARG2}","LoadDialPlan":"true"}`
- Отправляет CURL:
  - `RC=${CURL(https://bot.vochi.by/${API},${POST})}`
  - Заголовки: `Content-Type: application/json`, `Token: ${ID_TOKEN}`
- Парсит ответ, извлекает:
  - `CALLNAME` — название для CallerID
  - `NEXT` — имя контекста Smart Redirect (`mngr<internalId>_<extLine>_N`)
- Логирует результат в `/var/log/asterisk/event.log`.

## Сформированные пулом контексты назначения
Генератор `plan.py` создаёт полный пул контекстов Smart Redirect для каждой внешней линии (GSM и SIP) × каждого внутреннего номера:

- Для внутренних, чьи владельцы БЕЗ Follow Me:
  - Одношаговые контексты вида `mngr<internalId>_<extLine>_1`, которые звонят на `SIP/<internal>` 30 сек и уходят в fallback контекст линии.

- Для внутренних, чьи владельцы С Follow Me:
  - Цепочки из нескольких контекстов `mngr<internalId>_<extLine>_N` (N — номер шага follow me).
  - Правила шага:
    - Время дозвона: `rings × 5` секунд.
    - Внутренние номера в шаге → `SIP/<ext>`.
    - Внешние номера в шаге → `Local/<digits>@<исходящий_контекст_пользователя>`.
    - `LOCAL` выбирается: минимальный внутренний из номеров шага, иначе — минимальный внутренний пользователя.
    - Переход: на следующий шаг; на последнем шаге — `Goto(<fallback_ctx_for_ext_line>,${EXTEN},1)`.
  - Fallback контекст линии берётся из уже собранных переходов во `[from-out-office]`; если точного для линии нет — берётся первый доступный.

## Что видно сейчас на 0367
- В `/etc/asterisk/extensions.conf` (dialexecute):
  - `exten => 255,1,Goto(mngr25_1,${EXTEN},1)` — Follow Me номер пользователя (пример).
  - Присутствуют `[mngr25_1]`, `[mngr25_2]` — follow me из `dialexecute`.
- В `[from-out-office]` активны `Set(INCALL)`, `Macro(getcustomerdata,…)`, `GotoIf(${NEXT},…)` — крючок работает.
- В блоке Smart Redirection:
  - Для внутренних без Follow Me: одношаговые `mngr32_*`, `mngr37_*` и т.д.
  - Для внутренних с Follow Me (пример internal_id=31, user_id=25): двухшаговые `mngr31_<extLine>_1 → _2 → fallback` с правильным временем и наборами адресатов.

## Поток обработки входящего вызова (со стороны хоста)
1. Входящий вызов попадает в `[from-out-office]` по линии (напр. `0001363`).
2. Выполняется `Macro(getcustomerdata, ${CALLERID(num)}, ${EXTEN})`:
   - Формируется POST и уходит CURL на сервер (`https://bot.vochi.by/api/callevent/getcustomerdata`).
   - Сервер, зная правила, возвращает имя контекста Smart Redirect (например, `mngr31_0001363_1`).
3. При наличии `NEXT` — немедленно переход `Goto(${NEXT}, ${INCALL}, 1)` в выбранный контекст.
4. В Smart Redirect контексте выполняется звонок по шагу (SIP/внутренние, Local/внешние). По таймауту — переход на следующий шаг, затем в fallback линии.

## Точки диагностики
- `/var/log/asterisk/event.log` — результат работы макроса `getcustomerdata` (JSON запроса, ответ, статус, счётчик повторов).
- `/etc/asterisk/extensions_custom.conf` — логика CURL и парсинга ответа.
- `/etc/asterisk/extensions.conf` — актуальные контексты Smart Redirect и шапка `[from-out-office]`.

## Вывод
- На хосте реализована управляемая маршрутизация входящих вызовов: Asterisk запрашивает у сервера целевой Smart Redirect-контекст и отправляет вызов в соответствующий `mngr…`.
- Генератор диалплана формирует полный пул `mngr<internalId>_<extLine>_N`, включая Follow Me-цепочки, так что серверу достаточно вернуть имя требуемого контекста.

## To-Do (внедрение smart.py 8021)
- [x] Спецификация API smart.py (совместимая с макросом):
  - [x] POST `api/callevent/getcustomerdata` (в теле: UniqueId, Phone, TrunkId, LoadDialPlan)
  - [x] Ответ: `{ "Name": string|null, "DialPlan": string|null }`
  - [ ] SLA: ответ ≤ 300 мс; при ошибке/таймауте — DialPlan=null
  - [x] Аутентификация: заголовок `Token: ${ID_TOKEN}`, валидация и маппинг на юнит
- [x] Архитектура сервиса:
  - [x] FastAPI + pydantic, порт 8021
  - [x] Stateless с кэшем (TTL 30–60с)
    - [ ] соответствия internalId→userId, карта `internalId×extLine→mngr…` (из БД/`plan.py` логики)
    - [ ] follow_me_steps для юзеров, fallback-контексты внешних линий
  - [x] Правило выбора: заготовка — возвращать `mngr<internalId>_<extLine>_1` по алгоритмам first_call/last_call (по данным `calls`/`call_participants`); `retailcrm` — реализовано
- [x] Поддержка эксплуатации:
  - [x] Логирование решений (в `logs/smart_decisions.log`)
  - [x] Метрики (P95/latency, error rate, counters) — endpoint `/metrics`
  - [ ] Rate limit и allowlist IP от АТС
- [x] Интеграция в репозиторий:
  - [x] Скрипт управления `smart.sh` (start|stop|restart)
  - [x] Подключить в `all.sh` (старт/стоп всех сервисов)
  - [x] Добавить в `admin.py` модалку Services пункт управления smart (статус, start/stop/restart)
- [ ] Nginx:
  - [x] Проксировать запросы с внешки на 8021: `bot.vochi.by/api/callevent/getcustomerdata` → 8021
  - [ ] Сохранить совместимость с текущим путём макроса (опционально alias под старый URL)
  - [ ] Таймауты 1с, retry off, X-Forwarded-* заголовки
- [ ] Взаимодействие с Asterisk:
  - [x] На пилоте 0367 оставить макрос как есть, но путь вести на `bot.vochi.by` (nginx → 8021)
  - [ ] Опционально: временно дублировать запросы в тень (лог только) для сравнения решений
- [ ] Взаимодействие с БД/кэшем:
  - [ ] Источники: `users`, `user_internal_phones`, `dial_schemas`, `follow_me_steps`
  - [x] Кэширование (TTL в 8021; очистка при рестарте; отдельный `/cache/clear` — позже)
  - [x] TTL‑кэш по `Phone→Name` (60–300с) — реализовано в 8020 (`integration_cache.py`); также кэш ответственных (`Phone→Extension`)
- [ ] Режимы работы по юнитам:
  - [x] Режимы: `off` | `name-only` | `routing+name` (8021 читает `integrations_config.smart.mode`)
  - [x] Если `name-only`: всегда `DialPlan=null`, но `Name` заполняем (источник — выбранная интеграция)
  - [x] Если `routing+name`: возврат `DialPlan` реализован для first_call/last_call и retailcrm
  - [x] Переключатель режима на уровне предприятия (UI)
- [ ] Выбор интеграции (если активных несколько):
  - [x] В UI предприятия — список активных интеграций с radio "Primary for Smart" (основная для ответственного/имени)
  - [ ] Политика: primary → fallback по следующей в списке (ручной порядок)
  - [ ] 8021 читает политику через 8020: endpoint типа `/integrations/{enterprise}/smart-policy`
- [ ] UI/Настройки:
  - [x] В `enterprise_admin` добавить раздел "Smart":
    - [x] Переключатель режима (off/name-only/routing+name)
    - [x] Dropdown выбора основной интеграции (если несколько активных)
    - [ ] Кнопка "Тест вызова" (симулирует запрос для указанного номера)
  - [ ] В интеграционных модалках можно отображать флаг "Используется как Smart primary" (read-only), управление — только в разделе "Smart"
- [ ] Тестовый план:
  - [ ] Канареечный запуск на 0367 (1–2 дня), сравнение с ожидаемыми контекстами
  - [ ] Набор автотестов: выбор контекста по комбинациям (Follow Me / без, разные линии, режим name-only)
  - [ ] Роллбэк-план (отключение smart в один шаг)

## Сделано сверх плана
- Реализована интеграция 8021 ↔ 8020:
  - Endpoint 8020 `/customer-name/{enterprise}/{phone}` с TTL‑кэшем имён
  - Endpoint 8020 `/responsible-extension/{enterprise}/{phone}` с TTL‑кэшем внутренних; приоритет локальной мапы `user_extensions` над ответом RetailCRM
- В `retailcrm.py` добавлены внутренние эндпоинты:
  - `/internal/retailcrm/customer-name` — поиск клиента по телефону; формат имени «Фамилия Имя»
  - `/internal/retailcrm/responsible-extension` — ответственное лицо → внутренний (с fallback по поиску клиента)
- В `plan.py` изменена генерация:
  - Контексты Smart Redirect формируются ТОЛЬКО для линий, где `smart.enabled=true`
  - При отсутствии включённых линий блок SR в `extensions.conf` очищается до маркеров
- В UI линий (SIP/GSM):
  - Блок Smart Redirect с чекбоксами и селекторами порядка (1–3) для параметров "Имя линии/Имя клиента/Название магазина"
  - Жёсткое обеспечение уникальности позиций; сохранение и мгновенное обновление подсветки строки
  - Исправления JS: единое объявление `enforceGsmOrders`, рабочая кнопка "Сохранить"
- Регенирация диалплана:
  - Триггеры после сохранения SIP/GSM линии и при любом изменении пользователя (включая Follow Me)
- Логи и мониторинг:
  - Детальный лог решений `logs/smart_decisions.log`
  - Временная чистка крупного `debug.log`; добавить logrotate — в план
