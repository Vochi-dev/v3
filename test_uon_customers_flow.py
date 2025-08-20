#!/usr/bin/env python3
"""
Юнит-тесты U-ON → customers:
- type_id=3: создание клиента, запись ФИО и meta.ids.uon, person_uid
- type_id=4: обновление ФИО
- type_id=4: добавление второго номера → вторая строка с тем же person_uid

Тест использует запущенный сервис на 8022 и реальную БД PostgreSQL (localhost).
Все изменения ограничены тестовыми телефонами и в конце очищаются.
"""

import os
import time
import json
import requests
import psycopg2


DB_DSN = {
    "host": os.getenv("PGHOST", "127.0.0.1"),
    "port": int(os.getenv("PGPORT", "5432")),
    "dbname": os.getenv("PGDATABASE", "postgres"),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "r/Yskqh/ZbZuvjb2b3ahfg=="),
}

UON_WEBHOOK_URL = os.getenv("UON_WEBHOOK_URL", "http://localhost:8022/uon/webhook")


PHONE_MAIN = "+375297003134"
PHONE_ALT = "+375295556611"
UON_CLIENT_ID = "7"


def _db_exec(query: str, params=None):
    conn = psycopg2.connect(**DB_DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or [])
            try:
                return cur.fetchall()
            except psycopg2.ProgrammingError:
                return []
    finally:
        conn.close()


def _cleanup():
    _db_exec(
        "DELETE FROM customers WHERE phone_e164 IN (%s,%s)", [PHONE_MAIN, PHONE_ALT]
    )


def _select_customer(phone: str):
    rows = _db_exec(
        """
        SELECT id, enterprise_number, phone_e164,
               COALESCE(last_name,''), COALESCE(first_name,''), COALESCE(middle_name,''),
               meta->'ids'->'uon' AS ids_uon,
               meta->>'person_uid' AS person_uid
        FROM customers
        WHERE phone_e164=%s
        ORDER BY id DESC
        LIMIT 1
        """,
        [phone],
    )
    return rows[0] if rows else None


def _post_webhook(payload: dict):
    r = requests.post(UON_WEBHOOK_URL, data=payload, timeout=15)
    assert r.status_code == 200, r.text
    jr = r.json()
    assert jr.get("ok") is True, jr
    return jr


def test_uon_flow_create_update_add_phone():
    _cleanup()

    # 1) Создание (type_id=3)
    create_payload = {
        "uon_id": "67054",
        "uon_subdomain": "id67054",
        "type_id": "3",
        "user_id": "2",
        "client_id": UON_CLIENT_ID,
        "client": json.dumps(
            {
                "u_id": UON_CLIENT_ID,
                "u_name": "Тестирование",
                "u_surname": "Третье",
                "u_sname": "Создание",
                "u_phone": PHONE_MAIN,
                "u_phone_mobile": "",
                "u_phone_home": "",
            },
            ensure_ascii=False,
        ),
    }
    _post_webhook(create_payload)

    row = _select_customer(PHONE_MAIN)
    assert row is not None
    assert row[3] == "Третье" and row[4] == "Тестирование" and row[5] == "Создание"
    assert row[6] is not None and "7" in json.loads(row[6])
    assert row[7] == f"uon:{UON_CLIENT_ID}"

    # 2) Обновление ФИО (type_id=4)
    update_payload = {
        "uon_id": "67054",
        "uon_subdomain": "id67054",
        "type_id": "4",
        "user_id": "2",
        "client_id": UON_CLIENT_ID,
        "client": json.dumps(
            {
                "u_id": UON_CLIENT_ID,
                "u_name": "Четвертое",
                "u_surname": "Тестирование",
                "u_sname": "Редактирование",
                "u_phone": PHONE_MAIN,
            },
            ensure_ascii=False,
        ),
    }
    _post_webhook(update_payload)

    row2 = _select_customer(PHONE_MAIN)
    assert row2 is not None
    assert row2[3] == "Тестирование" and row2[4] == "Четвертое" and row2[5] == "Редактирование"
    assert row2[7] == f"uon:{UON_CLIENT_ID}"

    # 3) Добавление второго номера (type_id=4)
    add_phone_payload = {
        "uon_id": "67054",
        "uon_subdomain": "id67054",
        "type_id": "4",
        "user_id": "2",
        "client_id": UON_CLIENT_ID,
        "client": json.dumps(
            {
                "u_id": UON_CLIENT_ID,
                "u_name": "Четвертое",
                "u_surname": "Тестирование",
                "u_sname": "Редактирование",
                "u_phone": PHONE_ALT,
            },
            ensure_ascii=False,
        ),
    }
    _post_webhook(add_phone_payload)

    row_alt = _select_customer(PHONE_ALT)
    assert row_alt is not None
    assert row_alt[3] == "Тестирование" and row_alt[4] == "Четвертое" and row_alt[5] == "Редактирование"
    assert row_alt[7] == f"uon:{UON_CLIENT_ID}"

    # sanity: обе строки относятся к одному person_uid
    assert row2[7] == row_alt[7]

    # Финальная очистка
    _cleanup()


