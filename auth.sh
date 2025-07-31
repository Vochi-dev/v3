#!/bin/bash

# Скрипт для управления сервисом авторизации пользователей

APP_MODULE="auth:app"
PID_FILE="/var/run/auth_service.pid"
LOG_FILE="/var/log/auth_service.log"
HOST="0.0.0.0"
PORT="8015"

# Путь к uvicorn внутри venv
UVICORN_PATH="/root/asterisk-webhook/venv/bin/uvicorn"

start() {
    if [ -f $PID_FILE ]; then
        echo "🔐 Auth service is already running."
        return 1
    fi
    echo "🚀 Starting auth service on $HOST:$PORT..."
    # Запускаем uvicorn напрямую
    nohup $UVICORN_PATH $APP_MODULE --host $HOST --port $PORT >> $LOG_FILE 2>&1 &
    echo $! > $PID_FILE
    echo "✅ Auth service started."
}

stop() {
    if [ ! -f $PID_FILE ]; then
        echo "⚠️  Auth service is not running."
        return 1
    fi
    echo "🛑 Stopping auth service..."
    kill $(cat $PID_FILE)
    rm $PID_FILE
    echo "✅ Auth service stopped."
}

restart() {
    echo "🔄 Restarting auth service..."
    stop
    sleep 2
    start
}

status() {
    if [ -f $PID_FILE ]; then
        PID=$(cat $PID_FILE)
        if ps -p $PID > /dev/null 2>&1; then
            echo "✅ Auth service is running with PID $PID."
        else
            echo "❌ Auth service PID file exists but process is not running."
            rm $PID_FILE
        fi
    else
        echo "❌ Auth service is not running."
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
esac

exit 0 