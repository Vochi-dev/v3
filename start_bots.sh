#!/bin/bash
# start_bots.sh
# Скрипт для запуска сервисов ботов

cd /root/asterisk-webhook || exit 1

# Завершаем все процессы ботов (например, по имени бота, или через systemctl, если есть)
pkill -f bot.py

# Запускаем ботов в фоне с логированием
nohup python3 bot.py >> bots.log 2>&1 &

echo "Боты запущены"
