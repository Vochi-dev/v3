#!/usr/bin/env python3
import argparse
import asyncio
import configparser
import os
import re
import subprocess
import sys
from typing import Dict, List, Tuple

sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
from app.services.postgres import get_pool  # type: ignore


# Значения по умолчанию (могут быть переопределены флагами/hosts.ini/ENV)
DEFAULT_SSH_USER = os.environ.get('SIP_SSH_USER', 'root')
DEFAULT_SSH_PORT = int(os.environ.get('SIP_SSH_PORT', '5059'))
DEFAULT_SSH_PASSWORD = os.environ.get('SIP_SSH_PASSWORD', '5atx9Ate@pbx')


async def load_host_cfg(enterprise: str, args: argparse.Namespace) -> Dict[str, str]:
    cfg = {
        'host': args.host or '',
        'port': str(args.port) if args.port else str(DEFAULT_SSH_PORT),
        'user': args.user or DEFAULT_SSH_USER,
        'password': args.password or DEFAULT_SSH_PASSWORD,
        'path': args.path or '/etc/asterisk/sip_addproviders.conf',
    }
    ini_path = os.path.join(os.path.dirname(__file__), 'hosts.ini')
    if os.path.exists(ini_path):
        cp = configparser.ConfigParser()
        cp.read(ini_path)
        if cp.has_section(enterprise):
            for k in ('host', 'port', 'user', 'password', 'path'):
                if not cfg[k] and cp.has_option(enterprise, k):
                    cfg[k] = cp.get(enterprise, k)
    # если host не задан флагами/ini — пробуем получить IP из БД по enterprises.ip
    if not cfg['host']:
        pool = await get_pool()
        if not pool:
            raise SystemExit('Не удалось создать пул БД для получения IP предприятия')
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT ip FROM enterprises WHERE number=$1", enterprise)
            if row and row['ip']:
                cfg['host'] = str(row['ip']).strip()
    # validate
    missing = [k for k in ('host', 'port', 'user', 'password') if not cfg[k]]
    if missing:
        raise SystemExit(f"Недостаточно параметров подключения ({', '.join(missing)}). Укажите их флагами или в hosts.ini секции [{enterprise}].")
    return cfg


def fetch_remote_file(cfg: Dict[str, str]) -> str:
    cmd = [
        'sshpass', '-p', cfg['password'],
        'ssh', '-p', str(cfg['port']),
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        f"{cfg['user']}@{cfg['host']}",
        f"cat {cfg['path']}",
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise SystemExit(f"Не удалось прочитать {cfg['path']} с {cfg['host']}:{cfg['port']}: {res.stderr.strip()}")
    return res.stdout


def parse_internal_pairs(text: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    extn: str | None = None
    type_: str | None = None
    context: str | None = None
    secret: str | None = None

    def flush():
        nonlocal extn, type_, context, secret
        if extn and type_ == 'friend' and context == 'inoffice' and secret and re.fullmatch(r"\d{3}", extn):
            pairs.append((extn, secret))
        extn = type_ = context = secret = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            flush()
            extn = m.group(1)
            type_ = context = secret = None
            continue
        if line.startswith('type='):
            type_ = line.split('=', 1)[1].strip()
            continue
        if line.startswith('context='):
            context = line.split('=', 1)[1].strip()
            continue
        if line.startswith('secret='):
            secret = line.split('=', 1)[1].strip()
            continue
    flush()
    return pairs


async def sync_user_internal_phones(enterprise: str, pairs: List[Tuple[str, str]]):
    pool = await get_pool()
    if not pool:
        raise SystemExit("Не удалось получить подключение к БД")
    phone_to_pwd = dict(pairs)
    new_set = set(phone_to_pwd.keys())
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, phone_number FROM user_internal_phones WHERE enterprise_number=$1",
                enterprise,
            )
            exist_set = set(r['phone_number'] for r in rows)
            to_delete = list(exist_set - new_set)
            # delete removed
            if to_delete:
                await conn.execute(
                    "DELETE FROM user_internal_phones WHERE enterprise_number=$1 AND phone_number = ANY($2::text[])",
                    enterprise,
                    to_delete,
                )
            # upsert remaining/new
            for phone, pwd in pairs:
                tag = await conn.execute(
                    "UPDATE user_internal_phones SET password=$1 WHERE enterprise_number=$2 AND phone_number=$3",
                    pwd, enterprise, phone,
                )
                # tag like 'UPDATE 0' or 'UPDATE 1'
                if tag.endswith('0'):
                    await conn.execute(
                        "INSERT INTO user_internal_phones (user_id, phone_number, password, enterprise_number) VALUES (NULL, $1, $2, $3)",
                        phone, pwd, enterprise,
                    )


async def main() -> None:
    parser = argparse.ArgumentParser(description='Синхронизация внутренних номеров из Asterisk sip_addproviders.conf в user_internal_phones')
    parser.add_argument('enterprise', help='Номер юнита, например 0334')
    parser.add_argument('--host', help='Хост Asterisk')
    parser.add_argument('--port', type=int, help='SSH порт', default=None)
    parser.add_argument('--user', help='SSH пользователь')
    parser.add_argument('--password', help='SSH пароль')
    parser.add_argument('--path', help='Путь к sip_addproviders.conf', default=None)
    args = parser.parse_args()

    cfg = await load_host_cfg(args.enterprise, args)
    text = fetch_remote_file(cfg)
    pairs = parse_internal_pairs(text)
    if not pairs:
        print('Ничего не найдено (friend/inoffice с тремя цифрами). Выходим.')
        return
    await sync_user_internal_phones(args.enterprise, pairs)
    print(f"OK: синхронизация завершена для {args.enterprise}. Номеров: {len(pairs)}")


if __name__ == '__main__':
    asyncio.run(main())


