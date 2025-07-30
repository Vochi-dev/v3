#!/bin/bash

# SMS Sending Service Start Script
# Микросервис для отправки SMS через WebSMS API
# Порт: 8013

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

SERVICE_NAME="send_service_sms"
PID_FILE="${SERVICE_NAME}.pid"
LOG_FILE="${SERVICE_NAME}.log"
PYTHON_SCRIPT="${SERVICE_NAME}.py"

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "SMS Sending Service уже запущен (PID: $PID)"
            return 1
        else
            echo "Удаляю устаревший PID файл..."
            rm -f "$PID_FILE"
        fi
    fi
    
    echo "Запуск SMS Sending Service..."
    nohup python3 "$PYTHON_SCRIPT" > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    echo "SMS Sending Service запущен (PID: $PID)"
    echo "Лог файл: $LOG_FILE"
    echo "Сервис доступен на: http://localhost:8013"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "Остановка SMS Sending Service (PID: $PID)..."
            kill $PID
            sleep 2
            
            if ps -p $PID > /dev/null 2>&1; then
                echo "Принудительная остановка..."
                kill -9 $PID
            fi
            
            rm -f "$PID_FILE"
            echo "SMS Sending Service остановлен"
        else
            echo "SMS Sending Service не запущен"
            rm -f "$PID_FILE"
        fi
    else
        echo "PID файл не найден. SMS Sending Service вероятно не запущен"
    fi
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "SMS Sending Service запущен (PID: $PID)"
            echo "Порт: 8013"
            echo "Лог: $LOG_FILE"
            return 0
        else
            echo "SMS Sending Service не запущен (устаревший PID файл)"
            return 1
        fi
    else
        echo "SMS Sending Service не запущен"
        return 1
    fi
}

restart() {
    echo "Перезапуск SMS Sending Service..."
    stop
    sleep 1
    start
}

balance() {
    echo "Проверка баланса WebSMS..."
    python3 "$PYTHON_SCRIPT" balance
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
    balance)
        balance
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|balance}"
        exit 1
        ;;
esac 