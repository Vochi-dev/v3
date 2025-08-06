#!/bin/bash

# =============================================================================
# RetailCRM Integration Service Controller
# =============================================================================

SERVICE_NAME="retailcrm"
APP_MODULE="retailcrm:app"
HOST="0.0.0.0"
PORT="8019"
PID_FILE="/tmp/${SERVICE_NAME}.pid"
LOG_FILE="/root/asterisk-webhook/logs/${SERVICE_NAME}.log"

# Создаем директорию для логов если её нет
mkdir -p /root/asterisk-webhook/logs

# =============================================================================
# ФУНКЦИИ
# =============================================================================

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "⚠️ Сервис $SERVICE_NAME уже запущен (PID: $PID)"
            return 1
        else
            echo "🧹 Удаляем устаревший PID файл"
            rm -f "$PID_FILE"
        fi
    fi

    echo "🚀 Запуск $SERVICE_NAME на порту $PORT..."
    cd /root/asterisk-webhook
    
    # Активируем виртуальное окружение
    source /root/asterisk-webhook/venv/bin/activate
    
    # Запускаем сервис в фоне с логированием
    nohup /root/asterisk-webhook/venv/bin/python -m uvicorn "$APP_MODULE" \
      --host "$HOST" \
      --port "$PORT" \
      --log-level info \
      --log-config log_config.json >> "$LOG_FILE" 2>&1 &
    
    PID=$!
    echo $PID > "$PID_FILE"
    
    # Проверяем запуск
    sleep 2
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "✅ Сервис $SERVICE_NAME запущен успешно (PID: $PID)"
        echo "📁 Логи: $LOG_FILE"
        echo "🌐 URL: http://localhost:$PORT"
        return 0
    else
        echo "❌ Ошибка запуска $SERVICE_NAME"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "⚠️ PID файл не найден. Сервис $SERVICE_NAME не запущен"
        return 1
    fi

    PID=$(cat "$PID_FILE")
    
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "🛑 Остановка $SERVICE_NAME (PID: $PID)..."
        kill "$PID"
        
        # Ждем завершения процесса
        for i in {1..10}; do
            if ! ps -p "$PID" > /dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        
        # Принудительная остановка если нужно
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "🔥 Принудительная остановка..."
            kill -9 "$PID"
        fi
        
        rm -f "$PID_FILE"
        echo "✅ Сервис $SERVICE_NAME остановлен"
        return 0
    else
        echo "⚠️ Процесс с PID $PID не найден"
        rm -f "$PID_FILE"
        return 1
    fi
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "✅ Сервис $SERVICE_NAME запущен (PID: $PID)"
            echo "🌐 URL: http://localhost:$PORT"
            return 0
        else
            echo "❌ Сервис $SERVICE_NAME не запущен (устаревший PID файл)"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        echo "❌ Сервис $SERVICE_NAME не запущен"
        return 1
    fi
}

restart() {
    echo "🔄 Перезапуск $SERVICE_NAME..."
    stop
    sleep 2
    start
}

logs() {
    if [ -f "$LOG_FILE" ]; then
        echo "📋 Логи $SERVICE_NAME (последние 50 строк):"
        echo "=====================================/"
        tail -n 50 "$LOG_FILE"
    else
        echo "⚠️ Файл логов не найден: $LOG_FILE"
    fi
}

test() {
    echo "🧪 Тестирование RetailCRM API..."
    
    # Проверяем что сервис запущен
    if ! status > /dev/null 2>&1; then
        echo "❌ Сервис не запущен. Запускаем..."
        start
        sleep 3
    fi
    
    echo ""
    echo "🔸 1. Тест основной страницы..."
    curl -s "http://localhost:$PORT/" | python3 -m json.tool
    
    echo ""
    echo "🔸 2. Тест подключения к RetailCRM..."
    curl -s "http://localhost:$PORT/test/credentials" | python3 -m json.tool
    
    echo ""
    echo "🔸 3. Тест получения пользователей..."
    curl -s "http://localhost:$PORT/test/users" | python3 -m json.tool
    
    echo ""
    echo "✅ Тестирование завершено"
}

# =============================================================================
# ОСНОВНАЯ ЛОГИКА
# =============================================================================

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
    logs)
        logs
        ;;
    test)
        test
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|logs|test}"
        echo ""
        echo "Команды:"
        echo "  start   - Запустить сервис RetailCRM"
        echo "  stop    - Остановить сервис"
        echo "  restart - Перезапустить сервис"
        echo "  status  - Показать статус сервиса"
        echo "  logs    - Показать последние логи"
        echo "  test    - Протестировать API endpoints"
        exit 1
        ;;
esac

exit $?