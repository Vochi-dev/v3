import asyncio
import logging
from datetime import datetime
from collections import defaultdict
import phonenumbers
import re

from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.config import DB_PATH

import aiosqlite

# In-memory stores
dial_cache = {}
bridge_store = {}
active_bridges = {}

# История hangup и отображение пар звонков
call_pair_message_map = {}
hangup_message_map = defaultdict(list)

# ───────── Утилиты для номеров ─────────
def is_internal_number(number: str) -> bool:
    return bool(number and re.fullmatch(r"\d{3,4}", number))

def format_phone_number(phone: str) -> str:
    if not phone:
        return phone
    if is_internal_number(phone):
        return phone
    if not phone.startswith("+"):
        phone = "+" + phone
    try:
        parsed = phonenumbers.parse(phone, None)
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except:
        return phone

# ───────── Обновление истории ─────────
def update_call_pair_message(caller, callee, message_id, is_internal=False):
    key = tuple(sorted([caller, callee])) if is_internal else (caller,)
    call_pair_message_map[key] = message_id
    return key

def update_hangup_message_map(caller, callee, message_id, is_internal=False,
                              call_status=-1, call_type=-1, extensions=None):
    rec = {'message_id': message_id, 'caller': caller, 'callee': callee,
           'timestamp': datetime.now().isoformat(),
           'call_status': call_status, 'call_type': call_type,
           'extensions': extensions or []}
    hangup_message_map[caller].append(rec)
    if is_internal:
        hangup_message_map[callee].append(rec)
    hangup_message_map[caller] = hangup_message_map[caller][-5:]
    if is_internal:
        hangup_message_map[callee] = hangup_message_map[callee][-5:]

def get_relevant_hangup_message_id(caller, callee, is_internal=False):
    hist = (hangup_message_map.get(caller, []) + hangup_message_map.get(callee, [])) if is_internal else hangup_message_map.get(caller, [])
    if not hist:
        return None
    hist.sort(key=lambda x: x['timestamp'], reverse=True)
    return hist[0]['message_id']

def get_last_call_info(ext_num: str) -> str:
    hist = hangup_message_map.get(ext_num, [])
    if not hist:
        return ""
    last = sorted(hist, key=lambda x: x['timestamp'], reverse=True)[0]
    ts = datetime.fromisoformat(last['timestamp'])
    ts = ts.replace(hour=(ts.hour + 3) % 24)
    when = ts.strftime("%d.%m.%Y %H:%M")
    icon = "✅" if last['call_status'] == 2 else "❌"
    return (f"🛎️ Последний: {when}\n{icon}") if last['call_type'] == 0 else (f"⬆️ Последний: {when}\n{icon}")

# ───────── Обработчики Asterisk ─────────
# process_start, process_dial, process_bridge, process_hangup — без изменений,
# см. предыдущий полный текст, там всё корректно.

# ────────────────────────────────────────────────────────────────────────────────
async def _get_bot_and_recipients(asterisk_token: str) -> tuple[str, list[int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT number, bot_token FROM enterprises WHERE name2 = ?", (asterisk_token,))
        ent = await cur.fetchone()
        if not ent:
            raise HTTPException(status_code=404, detail="Unknown enterprise token")
        enterprise_id_str = ent["number"]
        bot_token = ent["bot_token"]
        cur = await db.execute(
            "SELECT telegram_id FROM enterprise_users WHERE enterprise_id = ? AND status = 'approved'",
            (enterprise_id_str,),
        )
        rows = await cur.fetchall()
    tg_ids = [int(r["telegram_id"]) for r in rows]
    return bot_token, tg_ids
