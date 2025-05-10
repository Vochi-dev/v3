from fastapi import FastAPI, Request
import logging
import json
import re
import phonenumbers
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Dispatcher, CallbackQueryHandler
from db import init_db, log_event

app = FastAPI()
init_db()

# ——— Настройка логирования ——————————————————————————————————————————————
logging.basicConfig(
    filename="asterisk_events.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ——— Настройки Telegram —————————————————————————————————————————————
TELEGRAM_BOT_TOKEN = "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA"
TELEGRAM_CHAT_ID   = "374573193"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ——— Dispatcher для inline-кнопок спойлера —————————————————————————————
dispatcher = Dispatcher(bot, None, use_context=True, workers=0)

# Словарь для хранения «спойлерных» деталей hangup по UID
hangup_details: dict[str, str] = {}

# ——— Хранилища промежуточных сообщений ——————————————————————————————
message_store      = {}  # для start
dial_store         = {}  # для dial
bridge_store       = {}  # для bridge
bridge_phone_index = {}  # для bridge по номеру
bridge_seen        = set()
dial_cache         = {}  # сохраняем call_type и extensions по uid

# ——— Утилиты ————————————————————————————————————————————————————————

def format_phone_number(phone: str) -> str:
    """
    Приводит телефон к формату +375 (XX) XXX-XX-XX.
    Если приходит 10-значный без кода, добавляет 375,
    если 11-значный начинающийся на 80, тоже правит на 375.
    """
    logging.info(f"Original phone: {phone}")
    if len(phone) == 11 and phone.startswith("80"):
        phone = "375" + phone[2:]
    elif len(phone) == 10 and phone.startswith("0"):
        phone = "375" + phone[1:]
    elif phone.startswith("+") and len(phone) > 10:
        return phone
    try:
        if not phone.startswith("+"):
            phone = "+" + phone
        parsed = phonenumbers.parse(phone, None)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        digits = e164[1:]
        cc = str(parsed.country_code)
        rest = digits[len(cc):]
        code = rest[:2]
        num  = rest[2:]
        return f"+{cc} ({code}) {num[:3]}-{num[3:5]}-{num[5:]}"
    except Exception:
        return phone

# ——— Обработчик inline-кнопки «Подробнее» —————————————————————————————

def on_more(update: Update, context):
    """
    При нажатии на кнопку «▶️ Подробнее» подменяет текст
    на полный спойлер detail для данного UID.
    """
    uid = update.callback_query.data
    detail = hangup_details.get(uid, "ℹ️ Нет подробностей.")
    update.callback_query.edit_message_text(text=detail, parse_mode="Markdown")

dispatcher.add_handler(CallbackQueryHandler(on_more))

# ——— Основной HTTP-эндпойнт для всех событий Asterisk ————————————————————

@app.post("/{event_type}")
async def receive_event(event_type: str, request: Request):
    data = await request.json()
    et   = event_type.lower()
    uid  = data.get("UniqueId", "")
    raw  = data.get("Phone") or data.get("CallerIDNum") or ""
    phone = format_phone_number(raw)

    # Логируем в БД и файл
    log_event(et, uid, json.dumps(data))
    logging.info(f"Event={et} UID={uid} raw={raw} data={data}")

    # ——— START ——————————————————————————————————————————————————————
    if et == "start":
        txt = f"🛎️ Входящий звонок\nАбонент: {phone}"
        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            message_store[uid] = sent.message_id
            logging.info(f"Sent START message for {uid}")
        except Exception as e:
            logging.error(f"START send error: {e}")
        return {"status": "sent"}

    # ——— DIAL ———————————————————————————————————————————————————————
    if et == "dial":
        ct   = int(data.get("CallType", 0))
        exts = data.get("Extensions", [])
        if ct == 1:
            txt = f"🛎️ Исходящий звонок\nМенеджер: {', '.join(map(str, exts))} ➡️ {phone}"
        else:
            txt = f"🛎️ Входящий звонок\nАбонент: {phone} ➡️ " + " ".join(f"🛎️{e}" for e in exts)

        # удаляем START, если был
        if uid in message_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, message_store.pop(uid))
            except: pass

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            dial_store[uid] = sent.message_id
            dial_cache[uid] = {"call_type": ct, "extensions": exts}
            logging.info(f"Sent DIAL message for {uid}")
        except Exception as e:
            logging.error(f"DIAL send error: {e}")
        return {"status": "sent"}

    # ——— BRIDGE —————————————————————————————————————————————————————
    if et == "bridge":
        caller    = data.get("CallerIDNum", "")
        connected = data.get("ConnectedLineNum", "")
        status    = int(data.get("CallStatus", 0))

        # игнорируем повтор и unknown
        if "<unknown>" in (caller, connected):
            return {"status": "ignored"}
        key = tuple(sorted((caller, connected)))
        if key in bridge_seen:
            return {"status": "ignored"}

        # определяем client/operator
        if re.fullmatch(r"\d{3}", caller):
            op, cli = caller, connected
        else:
            op, cli = connected, caller

        # проверяем, есть ли dial_cache
        dc = dial_cache.get(uid, {})
        ct = dc.get("call_type", 0)

        # префикс по типу
        if ct == 1 and status == 2:
            pre = "✅ Успешный исходящий звонок"
        elif ct == 0 and status == 2:
            pre = "✅ Успешный входящий звонок"
        else:
            pre = "🛎️ Идет разговор"

        txt = f"{pre}\nАбонент: {format_phone_number(cli)} ➡️ 🛎️{op}"

        # удаляем DIAL
        if uid in dial_store:
            try:
                await bot.delete_message(TELEGRAM_CHAT_ID, dial_store.pop(uid))
            except: pass

        try:
            sent = await bot.send_message(TELEGRAM_CHAT_ID, txt)
            bridge_store[uid] = sent.message_id
            bridge_phone_index[cli] = uid
            bridge_seen.add(key)
            logging.info(f"Sent BRIDGE message for {uid}")
        except Exception as e:
            logging.error(f"BRIDGE send error: {e}")
        return {"status": "sent"}

    # ——— HANGUP —————————————————————————————————————————————————————
    if et == "hangup":
        # удаляем все предыдущие по uid
        for store in (message_store, dial_store, bridge_store):
            if uid in store:
                try:
                    await bot.delete_message(TELEGRAM_CHAT_ID, store.pop(uid))
                except: pass

        # готовим данные
        st   = data.get("StartTime")
        etme = data.get("EndTime")
        cs   = int(data.get("CallStatus", -1))
        ct   = int(data.get("CallType",   -1))
        exts = data.get("Extensions", []) or dial_cache.get(uid, {}).get("extensions", [])

        # считаем длительность
        dur = ""
        try:
            s = datetime.fromisoformat(st)
            e = datetime.fromisoformat(etme)
            secs = int((e - s).total_seconds())
            dur = f"{secs//60:02}:{secs%60:02}"
        except:
            pass

        # форматируем заголовок (всегда видно)
        if ct == 0 and cs == 1:
            header = f"⬇️ ❌ Абонент положил трубку\nАбонент: {phone}"
        elif ct == 0 and cs == 0:
            header = f"⬇️ ❌ Неотвеченный звонок\nАбонент: {phone}"
        elif ct == 1 and cs == 0:
            header = f"⬆️ ❌ Абонент не ответил\nАбонент: {phone}"
        elif cs == 2:
            if ct == 0:
                header = f"⬇️ ✅ Успешный входящий звонок\nАбонент: {phone}"
            else:
                header = f"⬆️ ✅ Успешный исходящий звонок\nАбонент: {phone}"
        else:
            header = f"❌ Завершённый звонок\nАбонент: {phone}"

        # детали для спойлера
        detail = header
        if dur:
            detail += f"\n⌛ {dur}"
        if cs in (0, 1):  # показываем extensions
            for ex in exts:
                if ex:
                    detail += f" ☎️ {ex}"
        elif cs == 2:
            detail += f" 🔈 Запись"
            if exts:
                detail += f" ☎️ {exts[0]}"

        # сохраняем под uid
        hangup_details[uid] = detail

        # отправляем только header + кнопка
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Подробнее", callback_data=uid)]])
        await bot.send_message(TELEGRAM_CHAT_ID, header, reply_markup=keyboard)
        return {"status": "sent"}

    # ——— Прочие события —————————————————————————————————————————————————
    txt = f"📞 Event: {et}\n" + "\n".join(f"{k}: {v}" for k, v in data.items())
    await bot.send_message(TELEGRAM_CHAT_ID, txt)
    return {"status": "sent"}

# ——— Запускаем polling диспетчера callback’ов ——————————————————————————
@app.on_event("startup")
async def start_dispatcher():
    import threading
    threading.Thread(target=dispatcher.start, daemon=True).start()
