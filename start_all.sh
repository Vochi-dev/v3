#!/bin/bash
cd /root/asterisk-webhook

# Запускаем FastAPI uvicorn в фоне
nohup uvicorn main:app --host 0.0.0.0 --port 8001 --log-level debug > uvicorn.log 2>&1 &

# Запускаем Telegram-бота в фоне
nohup python3 app/telegram/bot.py > bot.log 2>&1 &

echo "FastAPI и Telegram-бот запущены"

