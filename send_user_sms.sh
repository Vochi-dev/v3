#!/bin/bash

# User SMS Sending Service Start Script
# Микросервис для отправки SMS от имени предприятий через WebSMS API
# Порт: 8014

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

SERVICE_NAME="send_user_sms"
PID_FILE="${SERVICE_NAME}.pid"
LOG_FILE="${SERVICE_NAME}.log"
PYTHON_SCRIPT="${SERVICE_NAME}.py"

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "User SMS Sending Service уже запущен (PID: $PID)"
            return 1
        else
            echo "Удаляю устаревший PID файл..."
            rm -f "$PID_FILE"
        fi
    fi
    
    echo "Запуск User SMS Sending Service..."
    nohup python3 "$PYTHON_SCRIPT" > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    echo "User SMS Sending Service запущен (PID: $PID)"
    echo "Лог файл: $LOG_FILE"
    echo "Сервис доступен на: http://localhost:8014"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "Остановка User SMS Sending Service (PID: $PID)..."
            kill $PID
            sleep 2
            
            if ps -p $PID > /dev/null 2>&1; then
                echo "Принудительная остановка..."
                kill -9 $PID
            fi
            
            rm -f "$PID_FILE"
            echo "User SMS Sending Service остановлен"
        else
            echo "User SMS Sending Service не запущен"
            rm -f "$PID_FILE"
        fi
    else
        echo "PID файл не найден. User SMS Sending Service вероятно не запущен"
    fi
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "User SMS Sending Service запущен (PID: $PID)"
            echo "Порт: 8014"
            echo "Лог: $LOG_FILE"
            return 0
        else
            echo "User SMS Sending Service не запущен (устаревший PID файл)"
            return 1
        fi
    else
        echo "User SMS Sending Service не запущен"
        return 1
    fi
}

restart() {
    echo "Перезапуск User SMS Sending Service..."
    stop
    sleep 1
    start
}

balance() {
    local enterprise_number="$1"
    
    if [ -z "$enterprise_number" ]; then
        echo "❌ Ошибка: Не указан номер предприятия"
        echo ""
        echo "Использование: $0 balance <номер_предприятия>"
        echo "Пример: $0 balance 0367"
        echo ""
        echo "Эта команда проверит баланс WebSMS для указанного предприятия"
        echo "используя credentials из поля custom_domain в таблице enterprises"
        exit 1
    fi
    
    echo "🔍 Проверка баланса WebSMS для предприятия $enterprise_number..."
    python3 "$PYTHON_SCRIPT" balance "$enterprise_number"
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
        balance "$2"
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|balance <enterprise_number>}"
        echo ""
        echo "User SMS Sending Service - отправка SMS от имени предприятий"
        echo "Порт: 8014"
        echo "Документация: websms_user_send.md"
        echo ""
        echo "Команды:"
        echo "  start                    - Запустить сервис"
        echo "  stop                     - Остановить сервис"
        echo "  restart                  - Перезапустить сервис"
        echo "  status                   - Проверить статус сервиса"
        echo "  balance <enterprise>     - Проверить баланс WebSMS предприятия"
        echo ""
        echo "Примеры:"
        echo "  $0 start"
        echo "  $0 balance 0367"
        exit 1
        ;;
esac 