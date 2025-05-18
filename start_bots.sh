#!/bin/bash
# start_bots.sh — запускает всех Telegram-ботов

cd /root/asterisk-webhook || exit 1

# Завершаем старые процессы
pkill -f app/telegram/bot.py

# Логируем
echo "[$(date)] Запуск ботов" >> bots.log

# Запускаем с правильным PYTHONPATH
nohup python3 -m app.telegram.bot >> bots.log 2>&1 &

# Ждём пару секунд
sleep 2

# Проверка, что бот реально стартанул
if pgrep -f app/telegram/bot.py > /dev/null; then
    echo "[$(date)] ✅ Боты успешно запущены" >> bots.log
else
    echo "[$(date)] ❌ Ошибка запуска ботов!" >> bots.log
fi
