#!/bin/bash

DB_PATH="/root/asterisk-webhook/asterisk_events.db"
LOG_FILE="/root/asterisk-webhook/bots.log"
BOT_SCRIPT="/root/asterisk-webhook/app/telegram/bot.py"

cd /root/asterisk-webhook || exit 1

echo "[$(date)] Попытка завершить процессы ботов..." >> "$LOG_FILE"
pkill -f "app.telegram.bot" >> "$LOG_FILE" 2>&1
sleep 1

echo "[$(date)] Проверяем, остались ли процессы ботов после pkill..." >> "$LOG_FILE"
pids=$(pgrep -f "app.telegram.bot")
if [ -n "$pids" ]; then
  echo "[$(date)] Остались процессы с PID: $pids, пробуем убить принудительно..." >> "$LOG_FILE"
  kill -9 $pids >> "$LOG_FILE" 2>&1
  sleep 1
else
  echo "[$(date)] Процессы ботов успешно завершены" >> "$LOG_FILE"
fi

echo "[$(date)] Запускаем ботов..." >> "$LOG_FILE"

# --- Вытаскиваем список enterprise_number с непустым bot_token из PostgreSQL ---
enterprise_numbers=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "SELECT number FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != '';" | xargs)

for number in $enterprise_numbers; do
  echo "[$(date)] Запуск бота для предприятия $number" >> "$LOG_FILE"
  nohup env PYTHONPATH=/root/asterisk-webhook python3 "$BOT_SCRIPT" --enterprise "$number" >> "$LOG_FILE" 2>&1 &
done

sleep 3

echo "[$(date)] Проверяем успешность запуска..." >> "$LOG_FILE"
if pgrep -f "app.telegram.bot" > /dev/null; then
    echo "[$(date)] ✅ Боты успешно запущены" >> "$LOG_FILE"
else
    echo "[$(date)] ❌ Ошибка запуска ботов!" >> "$LOG_FILE"
fi
