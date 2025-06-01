```python
import asyncio
import logging
from datetime import datetime
from collections import defaultdict
import re

import phonenumbers
import aiosqlite

from telegram import Bot
from telegram.error import BadRequest

from app.services.events import save_telegram_message
from app.config import DB_PATH

# ───────── ГЛОБАЛЬНОЕ СОСТОЯНИЕ ─────────
# Здесь хранятся все внутренние in-memory структуры,
# которые используются для сопоставления событий Asterisk и сообщений в Telegram.

# Кеш для события "dial": сохраняем информацию о звонке до момента перевода в "bridge"
dial_cache = {}

# Хранилище для сообщений, отправленных в Telegram при старте (start) или при переводе (bridge)
# Ключ: UniqueId звонка, значение: message_id в Telegram
bridge_store = {}

# Активные "мосты" (bridge): информационная структура, которая хранит текст сообщения,
# номера сторон и токен, для возможности периодической повторной отправки
active_bridges = {}

# Сопоставление пары "абонент–абонент" (caller–callee) с message_id в Telegram,
# чтобы можно было отвечать новым сообщением на предыдущее события
# Ключ: либо кортеж из двух внутренних номеров (для внутренних звонков),
# либо один номер (для внешних звонков). Значение: message_id.
call_pair_message_map = {}

# Хранилище истории hangup-событий: для каждого номера (caller) храним список последних записей
# Запись — это словарь с info о hangup: message_id, caller, callee, timestamp и т. д.
hangup_message_map = defaultdict(list)


# ───────── УТИЛИТЫ ─────────
def is_internal_number(number: str) -> bool:
    """
    Проверяет, является ли строка внутренним номером: 3 или 4 цифры без символов.
    Если number совпадает с шаблоном r"\d{3,4}", возвращаем True.
    """
    return bool(number and re.fullmatch(r"\d{3,4}", number))


def format_phone_number(phone: str) -> str:
    """
    Форматирует номер телефона в международный формат через библиотеку phonenumbers.
    Если передана внутренняя АТС-цифра (3-4 цифры) — возвращаем as is.
    Если не указан "+", добавляем его, парсим и форматируем.
    Если парсинг неудачный — возвращаем исходную строку.
    """
    if not phone:
        return phone
    # Если это внутренний номер, возвращаем без изменений
    if is_internal_number(phone):
        return phone
    # Добавляем "+" перед номером, если его нет
    if not phone.startswith("+"):
        phone = "+" + phone
    try:
        parsed = phonenumbers.parse(phone, None)
        # Форматируем в международный формат: +CC XXX XXX...
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except Exception:
        return phone


def update_call_pair_message(caller: str, callee: str, message_id: int, is_internal: bool = False):
    """
    Обновляет сопоставление пары звонка (caller–callee) и message_id.
    Для внутренних звонков ключ — кортеж sorted([caller, callee]),
    для внешних — только caller (входящий/исходящий).
    Возвращает ключ, который записан в call_pair_message_map.
    """
    if is_internal:
        key = tuple(sorted([caller, callee]))
    else:
        key = (caller,)
    call_pair_message_map[key] = message_id
    return key


def update_hangup_message_map(
    caller: str,
    callee: str,
    message_id: int,
    is_internal: bool = False,
    call_status: int = -1,
    call_type: int = -1,
    extensions: list = None
):
    """
    Добавляет запись о событии hangup в hangup_message_map для последующего поиска
    последнего hangup-сообщения, чтобы при новом событии можно было "reply_to" на него.
    Сохраняем только последние 5 записей для каждого номера.
    Если внутренний звонок — дублируем в историю обоих участников.
    """
    rec = {
        'message_id': message_id,
        'caller':      caller,
        'callee':      callee,
        'timestamp':   datetime.now().isoformat(),
        'call_status': call_status,
        'call_type':   call_type,
        'extensions':  extensions or []
    }
    # Добавляем запись для основного звонящего
    hangup_message_map[caller].append(rec)
    # Если внутренний, дублируем для другого участника (callee)
    if is_internal:
        hangup_message_map[callee].append(rec)

    # Оставляем только последние 5 hangup-сообщений для каждого номера
    hangup_message_map[caller] = hangup_message_map[caller][-5:]
    if is_internal:
        hangup_message_map[callee] = hangup_message_map[callee][-5:]


def get_relevant_hangup_message_id(caller: str, callee: str, is_internal: bool = False) -> int:
    """
    Ищет самый последний hangup-сообщение (message_id) для данного звонка.
    Для внутренних звонков объединяем списки историй для caller и callee, сортируем по timestamp
    и берём самое последнее. Для внешних — только история caller.
    Если ничего не найдено, возвращаем None.
    """
    if is_internal:
        hist = hangup_message_map.get(caller, []) + hangup_message_map.get(callee, [])
    else:
        hist = hangup_message_map.get(caller, [])
    if not hist:
        return None
    # Сортируем по timestamp (строка ISO формат), в обратном порядке (самый новый впереди)
    hist_sorted = sorted(hist, key=lambda x: x['timestamp'], reverse=True)
    return hist_sorted[0]['message_id']


def get_last_call_info(ext_num: str) -> str:
    """
    Возвращает строку с информацией о последнем завершённом (hangup) звонке для номера ext_num.
    Формат: иконка звонка, время в формате DD.MM.YYYY HH:MM (с учётом +3 к часовому поясу),
    а также иконка "успешно" или "неуспешно". Если нет истории для ext_num — возвращаем пустую строку.
    Если call_type == 0 (входящий), используем иконку 🛎️, иначе ⬆️.
    """
    hist = hangup_message_map.get(ext_num, [])
    if not hist:
        return ""
    # Берём самый последний hangup по timestamp
    last = sorted(hist, key=lambda x: x['timestamp'], reverse=True)[0]
    # Парсим timestamp и прибавляем 3 часа (UTC → местное время, если нужно)
    ts = datetime.fromisoformat(last['timestamp'])
    ts = ts.replace(hour=(ts.hour + 3) % 24)
    when = ts.strftime("%d.%m.%Y %H:%M")
    status = last['call_status']
    ctype = last['call_type']
    # Иконка успешного звонка, если status == 2, иначе ❌
    icon = "✅" if status == 2 else "❌"
    if ctype == 0:  # входящий звонок
        return f"🛎️ Последний: {when}\n{icon}"
    else:  # исходящий звонок
        return f"⬆️ Последний: {when}\n{icon}"


# ───────── ОБРАБОТЧИКИ СОБЫТИЙ ASTERISK ─────────

async def process_start(bot: Bot, chat_id: int, data: dict):
    """
    Обработчик события "start" (начало звонка):
    - Формирует текст: если внутренний звонок (CallType == 2), показываем номер и внутренний код.
      Иначе — входящий внешний: форматируем номер, показываем LastCallInfo.
    - Старая hangup-сообщение (если есть) найдётся через get_relevant_hangup_message_id,
      и новое сообщение отправится в ответ (reply_to). Иначе просто новое.
    - Сохраняем sent.message_id в bridge_store[uid], обновляем call_pair_message_map и hangup_message_map.
    - Сохраняем запись в БД через await save_telegram_message.
    """
    uid = data.get("UniqueId", "")
    # Raw phone может приходить в разных полях: "Phone" или "CallerIDNum"
    raw_phone = data.get("Phone", "") or data.get("CallerIDNum", "") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    # Определяем внутренний звонок: CallType == 2
    is_int = data.get("CallType", 0) == 2
    # Callee — в поле Extensions[0], если список Extensions непустой
    callee = exts[0] if exts else ""

    if is_int:
        # Для внутренних звонков показываем стрелку между участниками
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        # Для внешних входящих звонков: проверяем, не +000... (номер неизвестен)
        display = phone if not phone.startswith("+000") else "Номер не определен"
        text = f"🛎️ Входящий звонок\n💰 {display}"
        # Добавляем инфо о последнем звонке (если была история)
        last = get_last_call_info(raw_phone)
        if last:
            text += f"\n\n{last}"

    # Экранируем символы "<" и ">" для HTML-safe
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_start] => chat={chat_id}, text={safe_text!r}")

    try:
        # Пытаемся найти последний hangup-сообщение для reply_to
        reply_id = get_relevant_hangup_message_id(raw_phone, callee, is_int)
        if reply_id:
            sent = await bot.send_message(
                chat_id,
                safe_text,
                reply_to_message_id=reply_id,
                parse_mode="HTML"
            )
        else:
            sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_start] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем message_id, чтобы в дальнейшем можно было удалять/отвечать
    bridge_store[uid] = sent.message_id
    # Обновляем call_pair_message_map и hangup_message_map
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    # Сохраняем информацию о сообщении в БД
    await save_telegram_message(
        sent.message_id,
        "start",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )
    return {"status": "sent"}


async def process_dial(bot: Bot, chat_id: int, data: dict):
    """
    Обработчик события "dial" (начало набора):
    - Если при этом уже есть запись в bridge_store по uid, удаляем старое сообщение.
    - Формируем текст:
      * Если внутренний (call_type == 2): показываем участника и callee.
      * Иначе: если call_type == 1 (исходящий набирается), показываем номера Extensions ➡️ formatted phone.
        Иначе (входящий разговор) показываем formatted phone ➡️ Extensions.
      * Добавляем LastCallInfo (если есть).
    - Отправляем в Telegram новое сообщение, сохраняем его message_id в dial_cache[uid].
    - Обновляем call_pair_message_map, hangup_message_map, сохраняем в БД через await save_telegram_message.
    """
    uid = data.get("UniqueId", "")
    raw_phone = data.get("Phone", "") or ""
    phone = format_phone_number(raw_phone)
    exts = data.get("Extensions", [])
    call_type = int(data.get("CallType", 0))
    is_int = call_type == 2
    callee = exts[0] if exts else ""

    # Если для этого uid уже было стартовое или предыдущее сообщение, пытаемся его удалить
    if uid in bridge_store:
        try:
            await bot.delete_message(chat_id, bridge_store.pop(uid))
        except Exception:
            pass

    if is_int:
        # Внутренний звонок: показываем стрелку между номерами
        text = f"🛎️ Внутренний звонок\n{raw_phone} ➡️ {callee}"
    else:
        display = phone if not phone.startswith("+000") else "Номер не определен"
        if call_type == 1:
            # Исходящий звонок набирается: Extensions ➡️ отображаемый номер
            text = f"⬆️ <b>Набираем номер</b>\n☎️ {', '.join(exts)} ➡️\n💰 {display}"
        else:
            # Входящий звонок в состоянии разговора: отображаемый номер ➡️ Extensions
            lines = "\n".join(f"☎️ {e}" for e in exts)
            text = f"🛎️ <b>Входящий разговор</b>\n💰 {display} ➡️\n{lines}"
        # Добавляем информацию о последнем звонке:
        last = get_last_call_info(raw_phone if call_type != 1 else callee)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_dial] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_dial] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем в dial_cache данные о звонке, включая message_id
    dial_cache[uid] = {
        "caller":     raw_phone,
        "extensions": exts,
        "call_type":  call_type,
        "token":      data.get("Token", ""),
        "message_id": sent.message_id
    }
    # Обновляем сопоставления и сохраняем hangup-историю
    update_call_pair_message(raw_phone, callee, sent.message_id, is_int)
    update_hangup_message_map(raw_phone, callee, sent.message_id, is_int)

    await save_telegram_message(
        sent.message_id,
        "dial",
        data.get("Token", ""),
        raw_phone,
        callee,
        is_int
    )
    return {"status": "sent"}


async def process_bridge(bot: Bot, chat_id: int, data: dict):
    """
    Обработчик события "bridge" (соединение абонентов):
    - Если в dial_cache есть запись по uid, значит звонок переходит из набора в "мост".
      Удаляем из dial_cache и удаляем старое сообщение (bridge_store.get(uid)).
    - Определяем, внутренний звонок ли (caller и connected — обе цифры 3-4 длины).
    - Формируем текст:
      * Для внутренних: "⏱ Идет внутренний разговор\ncaller ➡️ connected"
      * Для внешних: если call_status == 2 (успешный), иконка ✅; иначе иконка ⬇️.
        Форматируем номера caller/connected, добавляем LastCallInfo для connected.
    - Отправляем новое сообщение, сохраняем message_id в bridge_store[uid].
    - Обновляем call_pair_message_map, hangup_message_map и сохраняем через await save_telegram_message.
    - Сохраняем в active_bridges для периодической репликации (resend_loop).
    """
    uid = data.get("UniqueId", "")
    caller = data.get("CallerIDNum", "")
    connected = data.get("ConnectedLineNum", "")
    # Определяем, оба ли номера внутренние (3-4 цифры)
    is_int = is_internal_number(caller) and is_internal_number(connected)

    # Если есть запись в dial_cache, значит предыдущее "dial"-сообщение нужно удалить
    if uid in dial_cache:
        # Удаляем запись он "dial"-звонке
        dial_cache.pop(uid, None)
        try:
            # Удаляем предыдущее сообщение из Telegram (bridge_store хранил ID от start или предыдущего bridge)
            await bot.delete_message(chat_id, bridge_store.get(uid, 0))
        except Exception:
            pass

    if is_int:
        text = f"⏱ Идет внутренний разговор\n{caller} ➡️ {connected}"
    else:
        status = int(data.get("CallStatus", 0))
        # Если call_status == 2, успешный разговор
        pre = "✅ Успешный разговор" if status == 2 else "⬇️ 💬 <b>Входящий разговор</b>"
        cli_f = format_phone_number(caller)
        cal_f = format_phone_number(connected)
        text = f"{pre}\n☎️ {cli_f} ➡️ 💰 {cal_f}"
        # Добавляем инфо о последнем звонке для connected
        last = get_last_call_info(connected)
        if last:
            text += f"\n\n{last}"

    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_bridge] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_bridge] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Сохраняем новое message_id в bridge_store
    bridge_store[uid] = sent.message_id
    # Обновляем call_pair_message_map и hangup_message_map
    update_call_pair_message(caller, connected, sent.message_id, is_int)
    update_hangup_message_map(caller, connected, sent.message_id, is_int)

    # Сохраняем информацию для повторной отправки (resend_loop)
    active_bridges[uid] = {
        "text":  safe_text,
        "cli":   caller,
        "op":    connected,
        "token": data.get("Token", "")
    }

    await save_telegram_message(
        sent.message_id,
        "bridge",
        data.get("Token", ""),
        caller,
        connected,
        is_int
    )
    return {"status": "sent"}


async def process_hangup(bot: Bot, chat_id: int, data: dict):
    """
    Обработчик события "hangup" (завершение звонка):
    - Удаляем все записи из bridge_store, dial_cache и active_bridges по uid.
    - Считаем длительность звонка (из StartTime и EndTime), форматируем в "MM:SS".
    - Формируем текст: если внутренний звонок (ct == 2 и callee внутри),
      показываем "✅ Успешный внутренний звонок" или "❌ Абонент не ответил" + номера и длительность.
      Иначе (внешний) проверяем ct и cs:
        * Если исходящий (ct == 1) и cs == 0 → "⬆️ ❌ Абонент не ответил"
        * Если cs == 2 → "✅ Завершённый звонок"
        * Иначе → "❌ Завершённый звонок"
      Добавляем formatted phone и длительность.
    - Отправляем сообщение, сохраняем message_id, обновляем call_pair_message_map и hangup_message_map,
      сохраняем через await save_telegram_message.
    """
    uid = data.get("UniqueId", "")
    caller = data.get("CallerIDNum", "")
    exts = data.get("Extensions", []) or []
    connected = data.get("ConnectedLineNum", "")
    # Определяем, внутренний ли звонок: если exts непуст, и первый элемент — внутренний номер
    is_int = bool(exts and is_internal_number(exts[0]))
    callee = exts[0] if exts else connected or ""

    # Убираем все старые cache-записи по uid
    bridge_store.pop(uid, None)
    # Если был запущен dial, удаляем
    dial_cache.pop(uid, None)
    # Если был активный мост, удаляем
    active_bridges.pop(uid, None)

    # Вычисляем длительность: StartTime и EndTime — ISO строки
    dur = ""
    try:
        start = datetime.fromisoformat(data.get("StartTime", ""))
        end = datetime.fromisoformat(data.get("EndTime", ""))
        secs = int((end - start).total_seconds())
        dur = f"{secs // 60:02}:{secs % 60:02}"
    except Exception:
        dur = ""

    phone = format_phone_number(caller)
    display = phone if not phone.startswith("+000") else "Номер не определен"
    cs = int(data.get("CallStatus", 0))
    ct = int(data.get("CallType", 0))

    if is_int:
        # Внутренний звонок: если cs == 2 (успешный), показываем ✅, иначе ❌
        if cs == 2:
            m = "✅ Успешный внутренний звонок\n"
        else:
            m = "❌ Абонент не ответил\n"
        m += f"{caller} ➡️ {callee}\n⌛ {dur}"
    else:
        # Внешний звонок:
        if ct == 1 and cs == 0:
            # Исходящий, абонент не ответил
            m = f"⬆️ ❌ Абонент не ответил\n💰 {display}"
        elif cs == 2:
            # Успешный завершённый
            m = f"✅ Завершённый звонок\n💰 {display}\n⌛ {dur}"
        else:
            # Неуспешный завершённый
            m = f"❌ Завершённый звонок\n💰 {display}\n⌛ {dur}"

    safe_text = m.replace("<", "&lt;").replace(">", "&gt;")
    logging.debug(f"[process_hangup] => chat={chat_id}, text={safe_text!r}")

    try:
        sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
    except BadRequest as e:
        logging.error(f"[process_hangup] send_message failed: {e}. text={safe_text!r}")
        return {"status": "error", "error": str(e)}

    # Обновляем сопоставления и hangup-историю
    update_call_pair_message(caller, callee, sent.message_id, is_int)
    update_hangup_message_map(caller, callee, sent.message_id, is_int, cs, ct, exts)

    await save_telegram_message(
        sent.message_id,
        "hangup",
        data.get("Token", ""),
        caller,
        callee,
        is_int
    )
    return {"status": "sent"}


async def create_resend_loop(dial_cache_arg, bridge_store_arg, active_bridges_arg, bot: Bot, chat_id: int):
    """
    Фоновая корутина для периодической пересылки active_bridges:
    Каждые 10 секунд проходим по всем активным "мостам" (bridge),
    удаляем старое сообщение (если есть), и шлём текст заново, чтобы не терять обновления.
    Обновляем bridge_store_arg[uid] новым message_id,
    и заново записываем историю hangup (update_hangup_message_map),
    а также сохраняем через await save_telegram_message.
    """
    while True:
        await asyncio.sleep(10)
        for uid, info in list(active_bridges_arg.items()):
            text = info.get("text", "")
            cli = info.get("cli")
            op = info.get("op")
            # Определяем, внутренний ли звонок
            is_int = is_internal_number(cli) and is_internal_number(op)
            # Ищем, можно ли reply_to на последний hangup
            reply_id = get_relevant_hangup_message_id(cli, op, is_int)

            safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
            logging.debug(f"[resend_loop] => chat={chat_id}, text={safe_text!r}")

            try:
                # Удаляем старое сообщение, если оно в bridge_store
                if uid in bridge_store_arg:
                    await bot.delete_message(chat_id, bridge_store_arg[uid])
                # Отправляем новое, с reply_to, если найден hangup
                if reply_id:
                    sent = await bot.send_message(
                        chat_id,
                        safe_text,
                        reply_to_message_id=reply_id,
                        parse_mode="HTML"
                    )
                else:
                    sent = await bot.send_message(chat_id, safe_text, parse_mode="HTML")
                # Обновляем bridge_store_arg[uid] новым message_id
                bridge_store_arg[uid] = sent.message_id
                # Перезаписываем hangup-историю
                update_hangup_message_map(cli, op, sent.message_id, is_int)
                # Сохраняем в БД как событие "bridge_resend"
                await save_telegram_message(
                    sent.message_id,
                    "bridge_resend",
                    info.get("token", ""),
                    cli,
                    op,
                    is_int
                )
            except BadRequest as e:
                logging.error(f"[resend_loop] failed for {uid}: {e}. text={safe_text!r}")


# ───────── ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ BOT И СПИСКА CHAT_ID ─────────

async def _get_bot_and_recipients(asterisk_token: str) -> tuple[str, list[int]]:
    """
    По Asterisk-Token (поле name2 в таблице enterprises) возвращает:
      - bot_token для Telegram
      - список всех verified tg_id из telegram_users,
        которые привязаны к этому bot_token и прошли верификацию.
    Если токен неизвестен, кидаем HTTPException(404).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_token FROM enterprises WHERE name2 = ?",
            (asterisk_token,)
        )
        ent = await cur.fetchone()
        if not ent:
            raise Exception("Unknown enterprise token")  # или HTTPException(404)
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


async def _dispatch_to_all(handler, body: dict):
    """
    Универсальный диспетчер для отправки сообщений всем подписанным чатам:
    - Сначала получаем bot_token и список chat_id через _get_bot_and_recipients.
    - Создаём Bot(token=bot_token), проходим по всем chat_id и вызываем handler(bot, chat_id, body).
    - Собираем результаты в список словарей {"chat_id": ..., "status": ...}, возвращаем в поле "delivered".
    """
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


# ───────── ТОЧКА ВХОДА ДЛЯ АПИ СЕРВЕРА ─────────
# Предполагается, что в вашем FastAPI или другом Framework
# при приёме HTTP-запроса с телом (JSON) body вы вызываете:
#   await _dispatch_to_all(process_start, body)      # для события "start"
#   await _dispatch_to_all(process_dial, body)       # для события "dial"
#   await _dispatch_to_all(process_bridge, body)     # для события "bridge"
#   await _dispatch_to_all(process_hangup, body)     # для события "hangup"
#
# Например, в FastAPI это может выглядеть так:
#
# @app.post("/asterisk/start")
# async def asterisk_start_endpoint(request: Request):
#     body = await request.json()
#     return await _dispatch_to_all(process_start, body)
#
# @app.post("/asterisk/dial")
# async def asterisk_dial_endpoint(request: Request):
#     body = await request.json()
#     return await _dispatch_to_all(process_dial, body)
#
# @app.post("/asterisk/bridge")
# async def asterisk_bridge_endpoint(request: Request):
#     body = await request.json()
#     return await _dispatch_to_all(process_bridge, body)
#
# @app.post("/asterisk/hangup")
# async def asterisk_hangup_endpoint(request: Request):
#     body = await request.json()
#     return await _dispatch_to_all(process_hangup, body)
#
# Если нужен цикл resend_loop, можно запустить его при старте приложения:
#
# async def on_startup():
#     bot_token, tg_ids = await _get_bot_and_recipients("<ваш-токен>")
#     bot = Bot(token=bot_token)
#     for chat_id in tg_ids:
#         asyncio.create_task(create_resend_loop(dial_cache, bridge_store, active_bridges, bot, chat_id))
#
# app.add_event_handler("startup", on_startup)
```
