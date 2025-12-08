#!/usr/bin/env python3
"""
Тестовый скрипт для ручного парсинга и отправки recovery событий

Использование:
  python test_recovery_parse.py 0367                    # Все неуспешные hangup за сегодня
  python test_recovery_parse.py 0367 --uniqueid 1234.567  # Конкретный звонок
  python test_recovery_parse.py 0367 --dry-run          # Только показать, не отправлять
  python test_recovery_parse.py 0367 --list             # Список доступных событий
"""

import asyncio
import argparse
import json
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Optional

# Добавляем путь к модулям download.py
sys.path.insert(0, '/root/asterisk-webhook')

from download import (
    get_active_enterprises,
    get_remote_failed_hangup_events,
    get_related_events_by_uniqueid,
    extract_internal_phone_from_related,
    parse_call_data,
    enrich_recovery_call_data,
    send_recovery_telegram_message,
    format_phone_number,
    is_internal_number,
    SSH_CONFIG
)


def get_all_hangup_events_from_host(enterprise_id: str, db_file: str) -> List[Dict]:
    """Получить ВСЕ события hangup (включая успешные) для тестирования"""
    enterprises = get_active_enterprises()
    config = enterprises.get(enterprise_id)
    if not config:
        print(f"❌ Предприятие {enterprise_id} не найдено")
        return []
    
    # Запрашиваем ВСЕ hangup события (без фильтра по статусу)
    cmd = f'''sshpass -p "{config["ssh_password"]}" ssh -p {config["ssh_port"]} -o StrictHostKeyChecking=no root@{config["ip"]} 'sqlite3 {db_file} "SELECT DateTime, Uniqueid, request, status FROM AlternativeAPIlogs WHERE event = \\"hangup\\" ORDER BY DateTime DESC LIMIT 20"' '''
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"❌ Ошибка: {result.stderr}")
            return []
        
        events = []
        for line in result.stdout.strip().split('\n'):
            if line and '|' in line:
                parts = line.split('|')
                if len(parts) >= 3:
                    datetime_str = parts[0]
                    unique_id = parts[1]
                    request_json = parts[2]
                    status = parts[3] if len(parts) > 3 else None
                    
                    try:
                        request_data = json.loads(request_json)
                        events.append({
                            'datetime': datetime_str,
                            'unique_id': unique_id,
                            'data': request_data,
                            'status': status
                        })
                    except json.JSONDecodeError:
                        pass
        return events
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return []


def print_event_summary(event: Dict, enterprise_id: str):
    """Красиво вывести информацию о событии"""
    data = event.get('data', {})
    
    call_type = int(data.get('CallType', 0))
    call_status = int(data.get('CallStatus', 0))
    
    type_names = {0: "Входящий", 1: "Исходящий", 2: "Внутренний"}
    status_names = {2: "✅ Успешный", 0: "❌ Пропущенный", 1: "❌ Неуспешный"}
    
    type_name = type_names.get(call_type, f"Неизв ({call_type})")
    status_name = status_names.get(call_status, f"❓ ({call_status})")
    
    extensions = data.get('Extensions', [])
    phone = data.get('Phone', '')
    trunk = data.get('Trunk', '')
    
    print(f"""
┌─────────────────────────────────────────────────────────────
│ UniqueId: {event.get('unique_id')}
│ Время:    {event.get('datetime')}
│ Тип:      {type_name}
│ Статус:   {status_name}
│ Телефон:  {format_phone_number(phone)}
│ Extensions: {extensions}
│ Trunk:    {trunk}
│ HTTP статус: {event.get('status', 'N/A')}
└─────────────────────────────────────────────────────────────""")


async def process_single_event(event: Dict, enterprise_id: str, dry_run: bool = False):
    """Обработать одно событие"""
    unique_id = event['unique_id']
    
    print(f"\n{'='*60}")
    print(f"🔄 Обработка события {unique_id}")
    print(f"{'='*60}")
    
    # Получаем связанные события
    today = datetime.now().strftime('%Y-%m-%d')
    db_file = f"/var/log/asterisk/Listen_AMI_{today}.db"
    
    print(f"\n📡 Получение связанных событий с хоста...")
    related_events = get_related_events_by_uniqueid(enterprise_id, db_file, unique_id)
    
    if related_events:
        print(f"✅ Найдено {len(related_events)} связанных событий:")
        for re in related_events:
            event_type = re.get('event')
            data = re.get('data', {})
            if event_type == 'dial':
                print(f"   - dial: Extensions={data.get('Extensions', [])}")
            elif event_type == 'bridge':
                print(f"   - bridge: CallerIDNum={data.get('CallerIDNum')}")
            elif event_type == 'bridge_leave':
                print(f"   - bridge_leave: CallerIDNum={data.get('CallerIDNum')}")
    else:
        print("⚠️ Связанные события не найдены")
    
    # Извлекаем internal_phone
    internal_phone = extract_internal_phone_from_related(related_events)
    print(f"\n📞 Извлечённый internal_phone: {internal_phone or 'не найден'}")
    
    # Парсим данные
    print(f"\n📊 Парсинг данных события...")
    call_data = parse_call_data(event, enterprise_id, related_events)
    
    print(f"   - phone_number: {call_data.get('phone_number')}")
    print(f"   - main_extension: {call_data.get('main_extension')}")
    print(f"   - call_type: {call_data.get('call_type')}")
    print(f"   - call_status: {call_data.get('call_status')}")
    print(f"   - duration: {call_data.get('duration')}s")
    print(f"   - trunk: {call_data.get('trunk')}")
    
    # Enrichment
    print(f"\n🔍 Обогащение данных...")
    enriched_data = await enrich_recovery_call_data(
        enterprise_number=enterprise_id,
        internal_phone=call_data.get('main_extension'),
        external_phone=call_data.get('phone_number'),
        trunk=call_data.get('trunk')
    )
    
    print(f"   - customer_name: {enriched_data.get('customer_name') or 'не найдено'}")
    print(f"   - manager_name: {enriched_data.get('manager_name') or 'не найдено'}")
    print(f"   - line_name: {enriched_data.get('line_name') or 'не найдено'}")
    
    # Формируем превью сообщения
    print(f"\n📝 Превью сообщения в Telegram:")
    print("-" * 50)
    preview = generate_message_preview(call_data, enriched_data)
    print(preview)
    print("-" * 50)
    
    if dry_run:
        print(f"\n⏸️ DRY RUN - сообщение НЕ отправлено")
    else:
        print(f"\n📤 Отправка в Telegram...")
        result = await send_recovery_telegram_message(call_data, enterprise_id, enriched_data)
        if result:
            print(f"✅ Сообщение успешно отправлено!")
        else:
            print(f"❌ Ошибка отправки сообщения")
    
    return call_data


def generate_message_preview(call_data: Dict, enriched_data: Dict) -> str:
    """Сгенерировать превью сообщения (без отправки)"""
    phone_number = call_data.get('phone_number', '')
    call_type = int(call_data.get('call_type', '0'))
    call_status = int(call_data.get('call_status', '0'))
    duration = call_data.get('duration', 0)
    start_time = call_data.get('start_time', '')
    main_extension = call_data.get('main_extension', '')
    call_url = call_data.get('call_url', '')
    trunk = call_data.get('trunk', '')
    
    customer_name = enriched_data.get('customer_name')
    manager_name = enriched_data.get('manager_name')
    line_name = enriched_data.get('line_name')
    
    is_incoming = call_type == 0
    is_outgoing = call_type == 1
    is_internal = call_type == 2
    is_answered = call_status == 2
    
    formatted_phone = format_phone_number(phone_number)
    display_phone = f"{formatted_phone} ({customer_name})" if customer_name else formatted_phone
    
    if main_extension and manager_name and not manager_name.startswith("Доб."):
        manager_display = f"{manager_name} ({main_extension})"
    elif main_extension:
        manager_display = main_extension
    else:
        manager_display = None
    
    duration_text = f"{duration//60:02d}:{duration%60:02d}" if duration > 0 else "00:00"
    
    time_part = "неизв"
    if start_time:
        try:
            if 'T' in start_time:
                time_part = start_time.split('T')[1][:5]
            elif ' ' in start_time:
                parts = start_time.split(' ')
                if len(parts) >= 2:
                    time_part = parts[1][:5]
        except:
            pass
    
    # Генерируем текст как в send_recovery_telegram_message
    if is_internal:
        if is_answered:
            text = f"✅🔄Успешный внутренний звонок\n☎️{manager_display or main_extension}➡️\n☎️{display_phone}"
        else:
            text = f"❌🔄Коллега не поднял трубку\n☎️{manager_display or main_extension}➡️\n☎️{display_phone}"
    elif is_incoming:
        if is_answered:
            text = f"✅🔄Успешный входящий звонок\n💰{display_phone}"
            if manager_display and is_internal_number(main_extension):
                text += f"\n☎️{manager_display}"
            if line_name:
                text += f"\n📡{line_name}"
            elif trunk:
                text += f"\nЛиния: {trunk}"
        else:
            text = f"❌🔄Мы не подняли трубку\n💰{display_phone}"
            if manager_display and is_internal_number(main_extension):
                text += f"\n☎️{manager_display}"
            if line_name:
                text += f"\n📡{line_name}"
            elif trunk:
                text += f"\nЛиния: {trunk}"
    else:  # outgoing
        if is_answered:
            text = f"✅🔄Успешный исходящий звонок"
            if manager_display and is_internal_number(main_extension):
                text += f"\n☎️{manager_display}"
            text += f"\n💰{display_phone}"
            if line_name:
                text += f"\n📡{line_name}"
            elif trunk:
                text += f"\nЛиния: {trunk}"
        else:
            text = f"❌🔄Абонент не поднял трубку"
            if manager_display and is_internal_number(main_extension):
                text += f"\n☎️{manager_display}"
            text += f"\n💰{display_phone}"
            if line_name:
                text += f"\n📡{line_name}"
            elif trunk:
                text += f"\nЛиния: {trunk}"
    
    text += f"\n⏰Начало звонка {time_part}"
    text += f"\n⌛ {'Длительность' if is_answered else 'Дозванивался'}: {duration_text}"
    
    if is_answered and call_url:
        text += f"\n🔉Запись разговора (ссылка)"
    
    return text


async def main():
    parser = argparse.ArgumentParser(description='Тестовый парсинг recovery событий')
    parser.add_argument('enterprise_id', help='Номер предприятия (например, 0367)')
    parser.add_argument('--uniqueid', '-u', help='Конкретный UniqueId для обработки')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Только показать, не отправлять')
    parser.add_argument('--list', '-l', action='store_true', help='Показать список доступных событий')
    parser.add_argument('--all', '-a', action='store_true', help='Все hangup (включая успешные)')
    
    args = parser.parse_args()
    
    enterprise_id = args.enterprise_id
    
    # Проверяем что предприятие существует
    enterprises = get_active_enterprises()
    if enterprise_id not in enterprises:
        print(f"❌ Предприятие {enterprise_id} не найдено")
        print(f"Доступные: {list(enterprises.keys())}")
        return
    
    print(f"\n🏢 Предприятие: {enterprise_id} ({enterprises[enterprise_id]['name']})")
    print(f"🖥️ IP: {enterprises[enterprise_id]['ip']}")
    
    today = datetime.now().strftime('%Y-%m-%d')
    db_file = f"/var/log/asterisk/Listen_AMI_{today}.db"
    print(f"📁 БД файл: {db_file}")
    
    if args.list or args.all:
        # Показать список событий
        print(f"\n📋 Последние 20 hangup событий:")
        events = get_all_hangup_events_from_host(enterprise_id, db_file)
        if events:
            for event in events:
                print_event_summary(event, enterprise_id)
        else:
            print("❌ События не найдены")
        return
    
    if args.uniqueid:
        # Обработать конкретный UniqueId
        print(f"\n🔍 Поиск события {args.uniqueid}...")
        events = get_all_hangup_events_from_host(enterprise_id, db_file)
        target_event = None
        for event in events:
            if event['unique_id'] == args.uniqueid:
                target_event = event
                break
        
        if target_event:
            await process_single_event(target_event, enterprise_id, args.dry_run)
        else:
            print(f"❌ Событие {args.uniqueid} не найдено")
    else:
        # Обработать неуспешные события
        print(f"\n📥 Получение неуспешных hangup событий...")
        events = get_remote_failed_hangup_events(enterprise_id, db_file)
        
        if not events:
            print("✅ Неуспешных событий не найдено")
            return
        
        print(f"📊 Найдено {len(events)} неуспешных событий")
        
        for event in events:
            print_event_summary(event, enterprise_id)
            
            response = input("\n🔄 Обработать это событие? [y/n/q]: ").strip().lower()
            if response == 'q':
                print("👋 Выход")
                break
            elif response == 'y':
                await process_single_event(event, enterprise_id, args.dry_run)
            else:
                print("⏭️ Пропущено")


if __name__ == "__main__":
    asyncio.run(main())

