#!/bin/bash

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

nohup python3 -m app.telegram.bot --enterprise 0100 >> bots.log 2>&1 &
nohup python3 -m app.telegram.bot --enterprise 0201 >> bots.log 2>&1 &
nohup python3 -m app.telegram.bot --enterprise 0262 >> bots.log 2>&1 &

sleep 3

echo "[$(date)] Проверяем успешность запуска..." >> bots.log
if pgrep -f "app.telegram.bot" > /dev/null; then
    echo "[$(date)] ✅ Боты успешно запущены" >> bots.log
else
    echo "[$(date)] ❌ Ошибка запуска ботов!" >> bots.log
fi
