#!/bin/bash

# Скрипт для управления сервисом админки предприятий

APP_MODULE="main:app"
PID_FILE="/var/run/enterprise_admin_service.pid"
LOG_FILE="/var/log/enterprise_admin_service.log"
HOST="0.0.0.0"
PORT="8004"

# Путь к uvicorn внутри venv
UVICORN_PATH="/root/asterisk-webhook/venv/bin/uvicorn"

start() {
    if [ -f $PID_FILE ]; then
        echo "Service is already running."
        return 1
    fi
    echo "Starting service on $HOST:$PORT..."
    # Запускаем uvicorn напрямую
    nohup $UVICORN_PATH $APP_MODULE --host $HOST --port $PORT >> $LOG_FILE 2>&1 &
    echo $! > $PID_FILE
    echo "Service started."
}

stop() {
    if [ ! -f $PID_FILE ]; then
        echo "Service is not running."
        return 1
    fi
    echo "Stopping service..."
    kill $(cat $PID_FILE)
    rm $PID_FILE
    echo "Service stopped."
}

restart() {
    stop
    sleep 2
    start
}

status() {
    if [ -f $PID_FILE ]; then
        echo "Service is running with PID $(cat $PID_FILE)."
    else
        echo "Service is not running."
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