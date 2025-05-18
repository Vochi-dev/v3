#!/bin/bash
# start_bots.sh — запускает всех Telegram-ботов с логированием

cd /root/asterisk-webhook || exit 1

echo "[$(date)] Попытка завершить процессы ботов..." >> bots.log
pkill -f "app.telegram.bot" >> bots.log 2>&1
sleep 1

echo "[$(date)] Проверяем, остались ли процессы ботов после pkill..." >> bots.log
pids=$(pgrep -f "app.telegram.bot")
if [ -n "$pids" ]; then
  echo "[$(date)] Остались процессы с PID: $pids, пробуем убить принудительно..." >> bots.log
  kill -9 $pids >> bots.log 2>&1
  sleep 1
else
  echo "[$(date)] Процессы ботов успешно завершены" >> bots.log
fi

echo "[$(date)] Запускаем ботов..." >> bots.log
nohup python3 -m app.telegram.bot >> bots.log 2>&1 &

sleep 3

echo "[$(date)] Проверяем успешность запуска..." >> bots.log
if ps aux | grep -v grep | grep -q "python3 -m app.telegram.bot"; then
    echo "[$(date)] ✅ Боты успешно запущены" >> bots.log
else
    echo "[$(date)] ❌ Ошибка запуска ботов!" >> bots.log
fi
