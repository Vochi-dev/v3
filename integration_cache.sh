#!/bin/bash

# Integration Cache Service Management Script
# Управление сервисом кэша интеграций на порту 8020

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SERVICE_NAME="integration_cache"
PORT=8020
PYTHON_FILE="integration_cache.py"
PID_FILE="${SERVICE_NAME}.pid"
LOG_FILE="logs/${SERVICE_NAME}.log"

# Создаем директорию для логов если не существует
mkdir -p logs

function is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        else
            rm -f "$PID_FILE"
            return 1
        fi
    fi
    return 1
}

function get_process_on_port() {
    lsof -ti:$PORT 2>/dev/null
}

function start() {
    echo "🚀 Запуск $SERVICE_NAME..."
    
    # Проверяем, не запущен ли уже сервис
    if is_running; then
        local pid=$(cat "$PID_FILE")
        echo "⚠️ Сервис уже запущен (PID: $pid)"
        return 1
    fi
    
    # Проверяем, не занят ли порт другим процессом
    local port_pid=$(get_process_on_port)
    if [[ -n "$port_pid" ]]; then
        echo "⚠️ Порт $PORT уже занят процессом $port_pid"
        echo "🛑 Завершаю процесс $port_pid..."
        kill -TERM "$port_pid" 2>/dev/null
        sleep 2
        
        # Принудительно убиваем если не завершился
        if ps -p "$port_pid" > /dev/null 2>&1; then
            kill -KILL "$port_pid" 2>/dev/null
        fi
    fi
    
    # Запускаем сервис
    export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"
    python3 -u "$PYTHON_FILE" > "$LOG_FILE" 2>&1 &
    local pid=$!
    
    # Сохраняем PID
    echo "$pid" > "$PID_FILE"
    
    # Проверяем, что процесс действительно запустился
    sleep 2
    if ps -p "$pid" > /dev/null 2>&1; then
        echo "✅ Сервис $SERVICE_NAME запущен успешно (PID: $pid)"
        echo "📁 Логи: $(realpath "$LOG_FILE")"
        echo "🌐 URL: http://localhost:$PORT"
        return 0
    else
        echo "❌ Не удалось запустить сервис"
        rm -f "$PID_FILE"
        return 1
    fi
}

function stop() {
    echo "🛑 Остановка $SERVICE_NAME..."
    
    local stopped=false
    
    # Останавливаем процесс из PID файла
    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE")
        echo "🔍 Найден процесс из PID-файла: $pid"
        
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "   Останавливаем PID $pid..."
            kill -TERM "$pid" 2>/dev/null
            
            # Ждем завершения
            for i in {1..10}; do
                if ! ps -p "$pid" > /dev/null 2>&1; then
                    stopped=true
                    break
                fi
                sleep 1
            done
            
            # Принудительно убиваем если не завершился
            if ps -p "$pid" > /dev/null 2>&1; then
                kill -KILL "$pid" 2>/dev/null
                stopped=true
            fi
        fi
        
        rm -f "$PID_FILE"
    fi
    
    # Проверяем процессы на порту
    local port_pids=$(get_process_on_port)
    if [[ -n "$port_pids" ]]; then
        echo "🔍 Найдены процессы на порту $PORT: $port_pids"
        
        for pid in $port_pids; do
            echo "   Останавливаем PID $pid..."
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            
            # Принудительно убиваем если не завершился
            if ps -p "$pid" > /dev/null 2>&1; then
                kill -KILL "$pid" 2>/dev/null
            fi
        done
        stopped=true
    fi
    
    if [[ "$stopped" == "true" ]]; then
        echo "✅ Сервис $SERVICE_NAME остановлен"
    else
        echo "ℹ️ Сервис $SERVICE_NAME не был запущен"
    fi
}

function restart() {
    echo "🔄 Перезапуск $SERVICE_NAME..."
    stop
    sleep 2
    start
}

function status() {
    if is_running; then
        local pid=$(cat "$PID_FILE")
        echo "✅ Сервис $SERVICE_NAME запущен (PID: $pid)"
        echo "📁 Логи: $(realpath "$LOG_FILE")"
        echo "🌐 URL: http://localhost:$PORT"
        
        # Проверяем HTTP доступность
        if curl -s "http://localhost:$PORT/health" > /dev/null 2>&1; then
            echo "🟢 HTTP сервер отвечает"
        else
            echo "🔴 HTTP сервер недоступен"
        fi
    else
        echo "🔴 Сервис $SERVICE_NAME не запущен"
        
        # Проверяем, не занят ли порт
        local port_pid=$(get_process_on_port)
        if [[ -n "$port_pid" ]]; then
            echo "⚠️ Порт $PORT занят процессом $port_pid"
        fi
    fi
}

function logs() {
    if [[ -f "$LOG_FILE" ]]; then
        echo "📋 Последние 20 строк логов:"
        tail -n 20 "$LOG_FILE"
    else
        echo "❌ Файл логов не найден: $LOG_FILE"
    fi
}

function health() {
    echo "🏥 Проверка здоровья сервиса..."
    
    if ! is_running; then
        echo "🔴 Сервис не запущен"
        return 1
    fi
    
    local response=$(curl -s "http://localhost:$PORT/health" 2>/dev/null)
    if [[ $? -eq 0 ]]; then
        echo "✅ Сервис отвечает:"
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
    else
        echo "🔴 Сервис не отвечает на HTTP запросы"
        return 1
    fi
}

function stats() {
    echo "📊 Статистика кэша..."
    
    local response=$(curl -s "http://localhost:$PORT/stats" 2>/dev/null)
    if [[ $? -eq 0 ]]; then
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
    else
        echo "❌ Не удалось получить статистику"
        return 1
    fi
}

function help() {
    echo "Использование: $0 {start|stop|restart|status|logs|health|stats|help}"
    echo ""
    echo "Команды:"
    echo "  start     - Запустить сервис"
    echo "  stop      - Остановить сервис"
    echo "  restart   - Перезапустить сервис"
    echo "  status    - Показать статус сервиса"
    echo "  logs      - Показать последние логи"
    echo "  health    - Проверить здоровье сервиса"
    echo "  stats     - Показать статистику кэша"
    echo "  help      - Показать эту справку"
}

# Main
case "${1:-}" in
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
    health)
        health
        ;;
    stats)
        stats
        ;;
    help|--help|-h)
        help
        ;;
    *)
        echo "❌ Неизвестная команда: ${1:-}"
        echo ""
        help
        exit 1
        ;;
esac

