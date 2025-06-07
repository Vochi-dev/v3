#!/bin/bash

# Скрипт для управления сервисом админки предприятий

SERVICE_NAME="enterprise_admin_service"
PID_FILE="/var/run/${SERVICE_NAME}.pid"
LOG_FILE="/var/log/${SERVICE_NAME}.log"
VENV_PATH="/root/asterisk-webhook/venv/bin/python"
SERVICE_FILE="/root/asterisk-webhook/enterprise_admin_service.py"

start() {
    if [ -f $PID_FILE ]; then
        echo "$SERVICE_NAME is already running."
        return 1
    fi
    echo "Starting $SERVICE_NAME..."
    nohup $VENV_PATH $SERVICE_FILE >> $LOG_FILE 2>&1 &
    echo $! > $PID_FILE
    echo "$SERVICE_NAME started."
}

stop() {
    if [ ! -f $PID_FILE ]; then
        echo "$SERVICE_NAME is not running."
        return 1
    fi
    echo "Stopping $SERVICE_NAME..."
    kill $(cat $PID_FILE)
    rm $PID_FILE
    echo "$SERVICE_NAME stopped."
}

restart() {
    stop
    sleep 2
    start
}

status() {
    if [ -f $PID_FILE ]; then
        echo "$SERVICE_NAME is running with PID $(cat $PID_FILE)."
    else
        echo "$SERVICE_NAME is not running."
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