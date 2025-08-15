import logging
from datetime import datetime
from typing import Optional

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


