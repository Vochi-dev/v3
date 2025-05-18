#!/bin/bash
# start_bots.sh — запускает всех Telegram-ботов

cd /root/asterisk-webhook || exit 1

# Завершаем старые процессы
pkill -f "app.telegram.bot"

# Лог
echo "[$(date)] Запуск ботов" >> bots.log

# Запуск
nohup python3 -m app.telegram.bot >> bots.log 2>&1 &

# Ждём
sleep 2

# Проверка по содержимому ps
if ps aux | grep -v grep | grep -q "python3 -m app.telegram.bot"; then
    echo "[$(date)] ✅ Боты успешно запущены" >> bots.log
else
    echo "[$(date)] ❌ Ошибка запуска ботов!" >> bots.log
fi
