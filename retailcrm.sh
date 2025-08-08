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
    # Поиск процессов по PID-файлу или вручную запущенных
    FOUND_PIDS=""
    
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            FOUND_PIDS="$PID"
            echo "🔍 Найден процесс из PID-файла: $PID"
        else
            echo "⚠️ PID из файла ($PID) не активен, удаляем файл"
            rm -f "$PID_FILE"
        fi
    fi
    
    # Дополнительный поиск процессов retailcrm по имени
    MANUAL_PIDS=$(pgrep -f "python.*retailcrm\.py" || true)
    if [ -n "$MANUAL_PIDS" ]; then
        echo "🔍 Найдены вручную запущенные процессы retailcrm: $MANUAL_PIDS"
        FOUND_PIDS="$FOUND_PIDS $MANUAL_PIDS"
    fi
    
    # Поиск uvicorn процессов на порту 8019
    UVICORN_PIDS=$(pgrep -f "uvicorn.*retailcrm.*8019" || true)
    if [ -n "$UVICORN_PIDS" ]; then
        echo "🔍 Найдены uvicorn процессы на порту 8019: $UVICORN_PIDS"
        FOUND_PIDS="$FOUND_PIDS $UVICORN_PIDS"
    fi
    
    # Удаляем дубликаты и пустые значения
    FOUND_PIDS=$(echo $FOUND_PIDS | tr ' ' '\n' | sort -u | grep -v '^$' | tr '\n' ' ')
    
    if [ -z "$FOUND_PIDS" ]; then
        echo "⚠️ Процессы $SERVICE_NAME не найдены"
        return 1
    fi

    # Остановка всех найденных процессов
    echo "🛑 Остановка $SERVICE_NAME (PIDs: $FOUND_PIDS)..."
    for PID in $FOUND_PIDS; do
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "   Останавливаем PID $PID..."
            kill "$PID"
        fi
    done
    
    # Ждем завершения процессов
    for i in {1..10}; do
        STILL_RUNNING=""
        for PID in $FOUND_PIDS; do
            if ps -p "$PID" > /dev/null 2>&1; then
                STILL_RUNNING="$STILL_RUNNING $PID"
            fi
        done
        if [ -z "$STILL_RUNNING" ]; then
            break
        fi
        sleep 1
    done
    
    # Принудительная остановка если нужно
    STILL_RUNNING=""
    for PID in $FOUND_PIDS; do
        if ps -p "$PID" > /dev/null 2>&1; then
            STILL_RUNNING="$STILL_RUNNING $PID"
        fi
    done
    
    if [ -n "$STILL_RUNNING" ]; then
        echo "🔥 Принудительная остановка процессов: $STILL_RUNNING"
        for PID in $STILL_RUNNING; do
            kill -9 "$PID" || true
        done
    fi
    
    rm -f "$PID_FILE"
    echo "✅ Сервис $SERVICE_NAME остановлен"
    return 0
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