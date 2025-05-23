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
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except Exception:
        return phone

# ───────── Обновление истории ─────────
def update_call_pair_message(caller, callee, message_id, is_internal=False):
    key = tuple(sorted([caller, callee])) if is_internal else (caller,)
    call_pair_message_map[key] = message_id
    return key

def update_hangup_message_map(caller, callee, message_id,
                              is_internal=False,
                              call_status=-1, call_type=-1,
                              extensions=None):
    rec = {
        'message_id': message_id,
        'caller':      caller,
        'callee':      callee,
        'timestamp':   datetime.now().isoformat(),
        'call_status': call_status,
        'call_type':   call_type,
        'extensions':  extensions or []
    }
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
    status = last['call_status']
    ctype  = last['call_type']
    icon = "✅" if status == 2 else "❌"
    if ctype == 0:
        return f"🛎️ Последний: {when}\n{icon}"
    else:
        return f"⬆️ Последний: {when}\n{icon}"

# ───────── Обработчики Asterisk ─────────

async def process_start(bot: Bot, chat_id: int, data: dict):
    uid       = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    phone     = format_phone_number(raw_phone)
    exts      = data.get("Extensions", [])
    is_int    = data.get("CallType", 0) == 2
    callee    = exts[0] if exts else ""

    if is_int:
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        text    = f"🛎️ Входящий звонок\n💰 {display}"
        last    = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    try:
        reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_int)
        if reply_id:
            sent = await bot.send_message(
                chat_id, safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_start] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    bridge_store[uid] = sent.message_id
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    await save_telegram_message(
        sent.message_id, "start", data.get("Token", ""),
        raw_phone, callee, is_int
    )
    return {"status": "sent"}


async def process_dial(bot: Bot, chat_id: int, data: dict):
    uid       = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    phone     = format_phone_number(raw_phone)
    exts      = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int    = call_type == 2
    callee    = exts[0] if exts else ""

    if uid in bridge_store:
        try:
            await bot.delete_message(chat_id, bridge_store.pop(uid))
        except:
            pass

    if is_int:
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        if call_type == 1:
            text = f"⬆️ <b>Набираем номер</b>\n☎️ {', '.join(exts)} ➡️\n💰 {display}"
        else:
            lines = "\n".join(f"☎️ {e}" for e in exts)
            text = f"🛎️ <b>Входящий разговор</b>\n💰 {display} ➡️\n{lines}"
        last = get_last_call_info(raw_phone if call_type != 1 else callee)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_dial] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_dial] failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    dial_cache[uid] = {
        "caller":     raw_phone,
        "extensions": exts,
        "call_type":  call_type,
        "token":      data.get("Token", "")
    }
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    await save_telegram_message(
        sent.message_id, "dial", data.get("Token", ""),
        raw_phone, callee, is_int
    )
    return {"status": "sent"}


async def process_bridge(bot: Bot, chat_id: int, data: dict):
    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    is_int    = is_internal_number(caller) and is_internal_number(connected)

    if uid in dial_cache:
        dial_cache.pop(uid)
        try:
            await bot.delete_message(chat_id, bridge_store.get(uid, 0))
        except:
            pass

    if is_int:
        text = f"⏱ Идет внутренний разговор\n{caller} ➡️ {connected}"
    else:
        status = int(data.get("CallStatus", 0))
        pre    = ("✅ Успешный разговор" if status == 2 else "⬇️ 💬 <b>Входящий разговор</b>")
        cli_f  = format_phone_number(caller)
        cal_f  = format_phone_number(connected)
        text   = f"{pre}\n☎️ {cli_f} ➡️ 💰 {cal_f}"
        last   = get_last_call_info(connected)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_bridge] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_bridge] failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    bridge_store[uid] = sent.message_id
    update_call_pair_message(caller, connected, sent.message_id, is_int)
    update_hangup_message_map(caller, connected, sent.message_id, is_int)

    active_bridges[uid] = {
        "text":  safe_text,
        "cli":   caller,
        "op":    connected,
        "token": data.get("Token", "")
    }

    await save_telegram_message(
        sent.message_id, "bridge", data.get("Token", ""),
        caller, connected, is_int
    )
    return {"status": "sent"}


async def process_hangup(bot: Bot, chat_id: int, data: dict):
    uid       = data.get("UniqueId", "")
    caller    = data.get("CallerIDNum", "")
    exts      = data.get("Extensions", []) or []
    connected = data.get("ConnectedLineNum", "")
    is_int    = bool(exts and is_internal_number(exts[0]))
    callee    = exts[0] if exts else connected or ""

    bridge_store.pop(uid, None)
    dial_cache.pop(uid, None)
    active_bridges.pop(uid, None)

    dur = ""
    try:
        start = datetime.fromisoformat(data.get("StartTime", ""))
        end   = datetime.fromisoformat(data.get("EndTime", ""))
        secs  = int((end - start).total_seconds())
        dur   = f"{secs//60:02}:{secs%60:02}"
    except:
        pass

    phone   = format_phone_number(caller)
    display = phone if not phone.startswith("+000") else "Номер не определен"
    cs = int(data.get("CallStatus", 0))
    ct = int(data.get("CallType", 0))

    if is_int:
        m  = ("✅ Успешный внутренний звонок\n" if cs == 2 else "❌ Абонент не ответил\n")
        m += f"{caller} ➡️ {callee}\n⌛ {dur}"
    else:
        if ct == 1 and cs == 0:
            m = f"⬆️ ❌ Абонент не ответил\n💰 {display}"
        elif cs == 2:
            m = f"✅ Завершённый звонок\n💰 {display}\n⌛ {dur}"
        else:
            m = f"❌ Завершённый звонок\n💰 {display}\n⌛ {dur}"

    safe_text = m.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_hangup] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_hangup] failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    update_call_pair_message(caller, callee, sent.message_id, is_int)
    update_hangup_message_map(caller, callee, sent.message_id, is_int,
                              cs, ct, exts)

    await save_telegram_message(
        sent.message_id, "hangup", data.get("Token", ""),
        caller, callee, is_int
    )
    return {"status": "sent"}


async def create_resend_loop(dial_cache_arg, bridge_store_arg, active_bridges_arg,
                             bot: Bot, chat_id: int):
    while True:
        await asyncio.sleep(10)
        for uid, info in list(active_bridges_arg.items()):
            text    = info.get("text", "")
            cli     = info.get("cli")
            op      = info.get("op")
            is_int  = is_internal_number(cli) and is_internal_number(op)
            reply_id= get_relevant_hangup_message_id(cli, op, is_int)

            safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
            logging.debug(f"[resend_loop] => chat={chat_id}, text={safe_text!r}")

            try:
                if uid in bridge_store_arg:
                    await bot.delete_message(chat_id, bridge_store_arg[uid])
                if reply_id:
                    sent = await bot.send_message(
                        chat_id, safe_text,
                        reply_to_message_id=reply_id,
                        parse_mode="HTML"
                    )
                else:
                    sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
                bridge_store_arg[uid] = sent.message_id
                update_hangup_message_map(cli, op, sent.message_id, is_int)
                await save_telegram_message(
                    sent.message_id, "bridge_resend",
                    info.get("token", ""),
                    cli, op, is_int
                )
            except BadRequest as e:
                logging.error(f"[resend_loop] failed for {uid}: {e}. text={safe_text!r}")


# ────────────────────────────────────────────────────────────────────────────────
async def _get_bot_and_recipients(asterisk_token: str) -> tuple[str, list[int]]:
    """
    По Asterisk-Token (поле name2 в enterprises) возвращает:
      - bot_token для Telegram
      - список всех verified telegram_id из telegram_users
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM enterprises WHERE name2 = ?",
            (asterisk_token,)
        )
        ent = await cur.fetchone()
        if not ent:
            raise HTTPException(status_code=404, detail="Unknown enterprise token")
        bot_token = ent["bot_token"]

        cur = await db.execute(
            """
            SELECT tg_id
              FROM telegram_users
             WHERE bot_token = ?
               AND verified = 1
            """,
            (bot_token,)
        )
        rows = await cur.fetchall()

    tg_ids = [int(r["tg_id"]) for r in rows]
    return bot_token, tg_ids


async def _dispatch_to_all(
    handler,
    body: dict
):
    token = body.get("Token")
    bot_token, tg_ids = await _get_bot_and_recipients(token)
    bot = Bot(token=bot_token)
    results = []

    for chat_id in tg_ids:
        try:
            await handler(bot, chat_id, body)
            results.append({"chat_id": chat_id, "status": "ok"})
        except Exception as e:
            logging.error(f"Asterisk dispatch to {chat_id} failed: {e}")
            results.append({"chat_id": chat_id, "status": "error", "error": str(e)})
    return {"delivered": results}
