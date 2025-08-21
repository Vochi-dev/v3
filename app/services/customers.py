import logging
import json
from datetime import datetime
from typing import Optional, Dict, Any

from app.services.postgres import get_pool


def _safe_parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _only_e164(phone: str) -> Optional[str]:
    if not phone:
        return None
    # Оставляем только плюс и цифры
    digits = ''.join(ch for ch in phone if ch.isdigit())
    if not digits:
        return None
    if phone.startswith('+'):
        return f"+{digits}"
    # Если уже нормализовано как +375..., добавим '+'
    return f"+{digits}"


def _is_internal(number: str) -> bool:
    if not number:
        return False
    return number.isdigit() and 3 <= len(number) <= 4


async def upsert_customer_from_hangup(data: dict) -> None:
    """
    Обновляет агрегаты клиента в таблице customers по событию hangup.
    Обрабатывает только внешние звонки (incoming/outgoing). Внутренние пропускает.
    """
    try:
        token = data.get("Token", "")
        if not token:
            return

        pool = await get_pool()
        if not pool:
            logging.error("[customers] PostgreSQL pool not available")
            return

        async with pool.acquire() as conn:
            # Получаем enterprise_number по токену (name2/secret/number)
            ent_row = await conn.fetchrow(
                """
                SELECT number FROM enterprises
                WHERE name2 = $1 OR secret = $1 OR number = $1
                LIMIT 1
                """,
                token,
            )
            enterprise_number = ent_row["number"] if ent_row else None
            if not enterprise_number:
                return

            call_type = int(data.get("CallType", -1))  # 0=in, 1=out, 2=internal
            call_status = int(data.get("CallStatus", -1))  # 2=answered, 0/other=missed/unknown
            exts = data.get("Extensions", []) or []
            caller = (data.get("CallerIDNum", "") or "").strip()
            connected = (data.get("ConnectedLineNum", "") or "").strip()
            trunk = (data.get("Trunk", "") or data.get("TrunkId", "") or data.get("INCALL", "") or "").strip()

            # Определяем направление и внешнего абонента, а также внутреннего агента
            direction = "unknown"
            external_phone = None
            internal_agent = None

            if call_type == 2:
                # Внутренние звонки не агрегируем в customers
                return
            elif call_type == 0:
                # incoming
                direction = "in"
                # Внешний номер берем из Phone/CallerIDNum
                external_phone = (data.get("Phone") or caller or "").strip()
                # Агент — connected если внутренний, иначе первый внутренний из exts
                if connected and _is_internal(connected):
                    internal_agent = connected
                else:
                    for ext in exts:
                        if _is_internal(ext):
                            internal_agent = ext
                            break
            elif call_type == 1:
                # outgoing
                direction = "out"
                # Внешний — connected если не внутренний, иначе ищем среди exts
                if connected and not _is_internal(connected):
                    external_phone = connected
                else:
                    for ext in exts:
                        if not _is_internal(ext):
                            external_phone = ext
                            break
                    if not external_phone:
                        external_phone = (data.get("Phone") or "").strip()
                # Агент — caller если он внутренний, иначе первый внутренний из exts
                if caller and _is_internal(caller):
                    internal_agent = caller
                else:
                    for ext in exts:
                        if _is_internal(ext):
                            internal_agent = ext
                            break
            else:
                # Неизвестный тип — не обновляем
                return

            # Попытка нормализовать внешний номер по incoming_transform из 8020 (как в 8000)
            try:
                import httpx
                if enterprise_number and trunk and (external_phone or caller or connected):
                    async with httpx.AsyncClient(timeout=1.5) as client:
                        r = await client.get(f"http://127.0.0.1:8020/incoming-transform/{enterprise_number}")
                        if r.status_code == 200:
                            m = (r.json() or {}).get("map") or {}
                            rule = m.get(f"sip:{trunk}") or m.get(f"gsm:{trunk}")
                            if isinstance(rule, str) and "{" in rule and "}" in rule:
                                pref = rule.split("{")[0]
                                try:
                                    n = int(rule.split("{")[1].split("}")[0])
                                except Exception:
                                    n = None
                                candidate = (external_phone or caller or connected or "").strip()
                                digits = ''.join(ch for ch in candidate if ch.isdigit())
                                if n and len(digits) >= n:
                                    external_phone = f"{pref}{digits[-n:]}"
            except Exception:
                pass

            phone_e164 = _only_e164(external_phone or "")
            if not phone_e164:
                return

            # Ответ — по статусу 2
            answered = (call_status == 2)
            now_ts = datetime.utcnow()
            end_dt = _safe_parse_dt(data.get("EndTime")) or now_ts

            # Дельты для инкремента
            total_in = 1 if direction == "in" else 0
            total_out = 1 if direction == "out" else 0
            ans_in = 1 if (direction == "in" and answered) else 0
            ans_out = 1 if (direction == "out" and answered) else 0
            miss_in = 1 if (direction == "in" and not answered) else 0
            miss_out = 1 if (direction == "out" and not answered) else 0

            last_call_status = "answered" if answered else "missed"

            sql = """
                INSERT INTO customers (
                    enterprise_number, phone_e164,
                    first_seen_at, last_seen_at, last_call_at,
                    last_call_direction, last_call_status, last_success_at,
                    last_agent_internal, last_line,
                    calls_total_in, calls_total_out,
                    calls_answered_in, calls_answered_out,
                    calls_missed_in, calls_missed_out
                ) VALUES (
                    $1, $2,
                    $3, $4, $5,
                    $6, $7, $8,
                    $9, $10,
                    $11, $12,
                    $13, $14,
                    $15, $16
                )
                ON CONFLICT (enterprise_number, phone_e164)
                DO UPDATE SET
                    last_seen_at = EXCLUDED.last_seen_at,
                    last_call_at = EXCLUDED.last_call_at,
                    last_call_direction = EXCLUDED.last_call_direction,
                    last_call_status = EXCLUDED.last_call_status,
                    last_agent_internal = EXCLUDED.last_agent_internal,
                    last_line = EXCLUDED.last_line,
                    calls_total_in = customers.calls_total_in + EXCLUDED.calls_total_in,
                    calls_total_out = customers.calls_total_out + EXCLUDED.calls_total_out,
                    calls_answered_in = customers.calls_answered_in + EXCLUDED.calls_answered_in,
                    calls_answered_out = customers.calls_answered_out + EXCLUDED.calls_answered_out,
                    calls_missed_in = customers.calls_missed_in + EXCLUDED.calls_missed_in,
                    calls_missed_out = customers.calls_missed_out + EXCLUDED.calls_missed_out,
                    first_seen_at = COALESCE(customers.first_seen_at, EXCLUDED.first_seen_at),
                    last_success_at = CASE WHEN EXCLUDED.last_success_at IS NOT NULL
                                           THEN EXCLUDED.last_success_at
                                           ELSE customers.last_success_at END
            """

            last_success_at = now_ts if answered else None
            await conn.execute(
                sql,
                enterprise_number, phone_e164,
                now_ts, now_ts, end_dt,
                direction, last_call_status, last_success_at,
                internal_agent, trunk,
                total_in, total_out,
                ans_in, ans_out,
                miss_in, miss_out,
            )
            logging.info(
                f"[customers] upsert ok ent={enterprise_number} phone={phone_e164} dir={direction} answered={answered}"
            )
    except Exception as e:
        logging.error(f"[customers] upsert failed: {e}")



async def merge_customer_identity(
    enterprise_number: str,
    phone_e164: str,
    source: str,
    external_id: str,
    fio: Optional[Dict[str, Optional[str]]] = None,
    set_primary: bool = False,
    person_uid: Optional[str] = None,
    source_raw: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Гарантирует наличие строки в customers и мержит идентичность клиента:
    - meta.ids.<source> += external_id (уникально)
    - meta.person_uid устанавливается, если пуст ("<source>:<external_id>")
    - при set_primary=true выставляет meta.primary_source=<source>
    - обновляет ФИО: если set_primary или поля пустые, заполняет переданными значениями
    """
    try:
        if not enterprise_number or not phone_e164 or not source or not external_id:
            return

        pool = await get_pool()
        if not pool:
            logging.error("[customers] PostgreSQL pool not available")
            return

        async with pool.acquire() as conn:
            # 1) Ensure row exists (do nothing on conflict)
            now_ts = datetime.utcnow()
            await conn.execute(
                """
                INSERT INTO customers (enterprise_number, phone_e164, first_seen_at, last_seen_at)
                VALUES ($1, $2, $3, $3)
                ON CONFLICT (enterprise_number, phone_e164) DO NOTHING
                """,
                enterprise_number, phone_e164, now_ts,
            )

            # 2) Read current meta and fio
            row = await conn.fetchrow(
                """
                SELECT id, meta, last_name, first_name, middle_name
                FROM customers
                WHERE enterprise_number=$1 AND phone_e164=$2
                LIMIT 1
                """,
                enterprise_number, phone_e164,
            )
            if not row:
                return

            meta: Dict[str, Any] = {}
            try:
                meta = dict(row["meta"]) if row["meta"] is not None else {}
            except Exception:
                meta = {}

            ids = meta.get("ids") or {}
            source_ids = list(ids.get(source) or [])
            if external_id not in source_ids:
                source_ids.append(external_id)
            ids[source] = source_ids
            meta["ids"] = ids

            if not meta.get("person_uid"):
                if person_uid:
                    meta["person_uid"] = person_uid
                elif source == "retailcrm_corporate":
                    meta["person_uid"] = f"retailcrm_corp:{external_id}"
                else:
                    meta["person_uid"] = f"{source}:{external_id}"
            elif person_uid and person_uid != meta.get("person_uid"):
                # Обновляем person_uid если передан новый
                meta["person_uid"] = person_uid

            if set_primary:
                meta["primary_source"] = source

            # Добавляем специальные поля для корпоративных клиентов
            if source == "retailcrm_corporate" and source_raw:
                company_info = source_raw.get("company_info", {})
                if company_info:
                    meta["client_type"] = "corporate"
                    meta["company_id"] = company_info.get("id")
                    meta["contact_id"] = company_info.get("contact_id")

            # 3) Merge FIO according to rules
            cur_ln = row["last_name"]
            cur_fn = row["first_name"]
            cur_mn = row["middle_name"]

            new_ln = None
            new_fn = None
            new_mn = None
            if fio:
                in_ln = (fio.get("last_name") or "").strip() or None
                in_fn = (fio.get("first_name") or "").strip() or None
                in_mn = (fio.get("middle_name") or "").strip() or None
                if set_primary:
                    new_ln = in_ln or cur_ln
                    new_fn = in_fn or cur_fn
                    new_mn = in_mn or cur_mn
                else:
                    new_ln = cur_ln or in_ln
                    new_fn = cur_fn or in_fn
                    new_mn = cur_mn or in_mn

            # 4) Update
            await conn.execute(
                """
                UPDATE customers
                SET meta = $3::jsonb,
                    last_name = COALESCE($4, last_name),
                    first_name = COALESCE($5, first_name),
                    middle_name = COALESCE($6, middle_name),
                    last_seen_at = $7
                WHERE enterprise_number=$1 AND phone_e164=$2
                """,
                enterprise_number,
                phone_e164,
                json.dumps(meta, ensure_ascii=False),
                new_ln,
                new_fn,
                new_mn,
                now_ts,
            )

            logging.info(
                f"[customers] identity merged ent={enterprise_number} phone={phone_e164} src={source} id={external_id} primary={set_primary}"
            )
    except Exception as e:
        logging.error(f"[customers] merge_identity failed: {e}")


async def update_fio_for_person(
    enterprise_number: str,
    person_uid: str,
    fio: Optional[Dict[str, Optional[str]]] = None,
    is_primary_source: bool = False,
) -> None:
    """
    Обновляет ФИО для всех записей клиента (по person_uid).
    - Если источник primary, то новое непустое значение замещает текущее.
    - Если не primary, то заполняем только пустые поля (fill-if-empty).
    """
    try:
        if not enterprise_number or not person_uid or not fio:
            return
        ln = (fio.get("last_name") or "").strip() or None
        fn = (fio.get("first_name") or "").strip() or None
        mn = (fio.get("middle_name") or "").strip() or None

        if not (ln or fn or mn):
            return

        pool = await get_pool()
        if not pool:
            logging.error("[customers] PostgreSQL pool not available")
            return

        async with pool.acquire() as conn:
            now_ts = datetime.utcnow()
            if is_primary_source:
                # Приоритетный источник: подменяем непустыми значениями
                await conn.execute(
                    """
                    UPDATE customers
                    SET last_name = COALESCE($3, last_name),
                        first_name = COALESCE($4, first_name),
                        middle_name = COALESCE($5, middle_name),
                        last_seen_at = $6
                    WHERE enterprise_number = $1
                      AND meta ->> 'person_uid' = $2
                    """,
                    enterprise_number, person_uid, ln, fn, mn, now_ts,
                )
            else:
                # Не приоритетный: только заполнение пустых
                await conn.execute(
                    """
                    UPDATE customers
                    SET last_name = COALESCE(last_name, $3),
                        first_name = COALESCE(first_name, $4),
                        middle_name = COALESCE(middle_name, $5),
                        last_seen_at = $6
                    WHERE enterprise_number = $1
                      AND meta ->> 'person_uid' = $2
                    """,
                    enterprise_number, person_uid, ln, fn, mn, now_ts,
                )
            logging.info(
                f"[customers] fio updated for person_uid ent={enterprise_number} uid={person_uid} primary={is_primary_source}"
            )
    except Exception as e:
        logging.error(f"[customers] update_fio_for_person failed: {e}")

