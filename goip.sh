#!/bin/bash

# Скрипт для управления GoIP сервисом

APP_MODULE="goip_service:app"
PID_FILE="/var/run/goip_service.pid"
LOG_FILE="/var/log/goip_service.log"
HOST="127.0.0.1"
PORT="8008"

# Путь к uvicorn внутри venv
UVICORN_PATH="/root/asterisk-webhook/venv/bin/uvicorn"

start() {
    if [ -f $PID_FILE ]; then
        echo "GoIP Service is already running."
        return 1
    fi
    echo "Starting GoIP Service on $HOST:$PORT..."
    # Запускаем uvicorn напрямую
    setsid nohup $UVICORN_PATH $APP_MODULE --host $HOST --port $PORT >> $LOG_FILE 2>&1 &
    echo $! > $PID_FILE
    echo "GoIP Service started."
}

stop() {
    if [ ! -f $PID_FILE ]; then
        echo "GoIP Service is not running."
        return 1
    fi
    echo "Stopping GoIP Service..."
    kill $(cat $PID_FILE)
    rm $PID_FILE
    echo "GoIP Service stopped."
}

restart() {
    stop
    sleep 2
    start
}

status() {
    if [ -f $PID_FILE ]; then
        echo "GoIP Service is running with PID $(cat $PID_FILE)."
    else
        echo "GoIP Service is not running."
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