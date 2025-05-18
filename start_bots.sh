#!/bin/bash
# start_bots.sh — перезапускает ботов

cd /root/asterisk-webhook || exit 1

# Завершаем старые процессы
pkill -f bot.py

# Запускаем бота из правильного пути, логируем
echo "Запуск ботов: $(date)" >> bots.log
nohup python3 app/telegram/bot.py >> bots.log 2>&1 &

sleep 2

# Проверка запуска
if pgrep -f app/telegram/bot.py > /dev/null; then
    echo "✅ Боты успешно запущены" >> bots.log
else
    echo "❌ Ошибка запуска ботов!" >> bots.log
fi
