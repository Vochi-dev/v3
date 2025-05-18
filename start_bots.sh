#!/bin/bash
# start_bots.sh — перезапускает всех Telegram-ботов

cd /root/asterisk-webhook || exit 1

# Завершаем старые процессы
pkill -f app/telegram/bot.py

# Запускаем с правильным PYTHONPATH
echo "[$(date)] Запуск ботов" >> bots.log
nohup PYTHONPATH=. python3 app/telegram/bot.py >> bots.log 2>&1 &

# Немного ждём
sleep 2

# Проверка, что бот реально стартанул
if pgrep -f app/telegram/bot.py > /dev/null; then
    echo "[$(date)] ✅ Боты успешно запущены" >> bots.log
else
    echo "[$(date)] ❌ Ошибка запуска ботов!" >> bots.log
fi
