#!/bin/bash

# Конфигурация
SERVICE_NAME="miniapp"
SERVICE_DISPLAY_NAME="Mini App Service"
SERVICE_FILE="mini_app/miniapp_service.py"
SERVICE_PORT=8017
LOG_FILE="logs/${SERVICE_NAME}_service.log"

# Функция для получения PID процесса
get_pid() {
    pgrep -f "$SERVICE_FILE"
}

# Функция для проверки статуса
check_status() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        echo "✅ $SERVICE_DISPLAY_NAME запущен (PID: $pid)"
        return 0
    else
        echo "❌ $SERVICE_DISPLAY_NAME не запущен"
        return 1
    fi
}

# Функция запуска
start_service() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        echo "⚠️  $SERVICE_DISPLAY_NAME уже запущен (PID: $pid)"
        return 1
    fi
    
    echo "🚀 Запускаем $SERVICE_DISPLAY_NAME..."
    
    # Создаем директорию для логов если её нет
    mkdir -p logs
    
    # Запускаем сервис
    cd /root/asterisk-webhook
    setsid nohup python3 $SERVICE_FILE > $LOG_FILE 2>&1 &
    
    sleep 2
    
    local new_pid=$(get_pid)
    if [ -n "$new_pid" ]; then
        echo "✅ $SERVICE_DISPLAY_NAME запущен на порту $SERVICE_PORT (PID: $new_pid)"
        return 0
    else
        echo "❌ Не удалось запустить $SERVICE_DISPLAY_NAME"
        echo "Последние строки лога:"
        tail -5 $LOG_FILE 2>/dev/null || echo "Лог не найден"
        return 1
    fi
}

# Функция остановки
stop_service() {
    local pid=$(get_pid)
    if [ -z "$pid" ]; then
        echo "⚠️  $SERVICE_DISPLAY_NAME не запущен"
        return 1
    fi
    
    echo "🛑 Останавливаем $SERVICE_DISPLAY_NAME (PID: $pid)..."
    
    # Останавливаем процесс
    kill $pid
    
    # Ждем завершения процесса
    local count=0
    while [ $count -lt 10 ]; do
        if [ -z "$(get_pid)" ]; then
            echo "✅ $SERVICE_DISPLAY_NAME остановлен"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    
    # Принудительная остановка
    echo "⚠️  Принудительная остановка $SERVICE_DISPLAY_NAME..."
    kill -9 $pid 2>/dev/null
    
    if [ -z "$(get_pid)" ]; then
        echo "✅ $SERVICE_DISPLAY_NAME принудительно остановлен"
        return 0
    else
        echo "❌ Не удалось остановить $SERVICE_DISPLAY_NAME"
        return 1
    fi
}

# Функция перезапуска
restart_service() {
    echo "🔄 Перезапуск $SERVICE_DISPLAY_NAME..."
    stop_service
    sleep 2
    start_service
}

# Функция просмотра логов
show_logs() {
    local lines=${1:-20}
    if [ -f "$LOG_FILE" ]; then
        echo "📋 Последние $lines строк лога $SERVICE_DISPLAY_NAME:"
        tail -n $lines $LOG_FILE
    else
        echo "❌ Лог файл не найден: $LOG_FILE"
    fi
}

# Функция тестирования
test_service() {
    echo "🧪 Тестируем $SERVICE_DISPLAY_NAME..."
    
    # Проверяем что сервис запущен
    if ! check_status >/dev/null; then
        echo "❌ Сервис не запущен"
        return 1
    fi
    
    # Тестируем HTTP endpoint
    echo "🌐 Тестируем HTTP endpoint..."
    local response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$SERVICE_PORT/health)
    
    if [ "$response" = "200" ]; then
        echo "✅ HTTP endpoint отвечает (код: $response)"
        
        # Тестируем главную страницу
        echo "📱 Тестируем главную страницу Mini App..."
        local main_response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$SERVICE_PORT/)
        
        if [ "$main_response" = "200" ]; then
            echo "✅ Главная страница доступна (код: $main_response)"
            echo "🎯 $SERVICE_DISPLAY_NAME работает корректно!"
            return 0
        else
            echo "❌ Главная страница недоступна (код: $main_response)"
            return 1
        fi
    else
        echo "❌ HTTP endpoint не отвечает (код: $response)"
        return 1
    fi
}

# Функция очистки портов
clean_port() {
    echo "🧹 Очистка порта $SERVICE_PORT..."
    local pids=$(lsof -ti:$SERVICE_PORT 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "🔫 Убиваем процессы на порту $SERVICE_PORT: $pids"
        kill -9 $pids
        echo "✅ Порт $SERVICE_PORT освобожден"
    else
        echo "✅ Порт $SERVICE_PORT свободен"
    fi
}

# Основная логика
case "$1" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        check_status
        ;;
    logs)
        show_logs $2
        ;;
    test)
        test_service
        ;;
    clean)
        clean_port
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|logs [N]|test|clean}"
        echo ""
        echo "Команды:"
        echo "  start    - Запустить $SERVICE_DISPLAY_NAME"
        echo "  stop     - Остановить $SERVICE_DISPLAY_NAME"
        echo "  restart  - Перезапустить $SERVICE_DISPLAY_NAME"
        echo "  status   - Проверить статус $SERVICE_DISPLAY_NAME"
        echo "  logs [N] - Показать последние N строк лога (по умолчанию 20)"
        echo "  test     - Протестировать работу $SERVICE_DISPLAY_NAME"
        echo "  clean    - Очистить порт $SERVICE_PORT"
        echo ""
        echo "Порт: $SERVICE_PORT"
        echo "Лог:  $LOG_FILE"
        exit 1
        ;;
esac

exit $?