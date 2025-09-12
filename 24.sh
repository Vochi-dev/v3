#!/bin/bash

# Bitrix24 Integration Service Management Script
# Port: 8024

SERVICE_NAME="bitrix24"
SERVICE_FILE="24.py"
SERVICE_PORT="8024"
PID_FILE="pids/${SERVICE_NAME}.pid"
LOG_FILE="logs/${SERVICE_NAME}.log"

# Создаем директории если не существуют
mkdir -p pids logs

case "$1" in
    start)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "⚠️  $SERVICE_NAME уже запущен (PID: $PID)"
                exit 1
            else
                echo "🧹 Удаляем устаревший PID файл"
                rm -f "$PID_FILE"
            fi
        fi
        
        echo "🚀 Запуск $SERVICE_NAME на порту $SERVICE_PORT..."
        nohup python3 "$SERVICE_FILE" > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        
        # Проверяем запуск
        sleep 2
        if ps -p $(cat "$PID_FILE") > /dev/null 2>&1; then
            echo "✅ $SERVICE_NAME успешно запущен (PID: $(cat "$PID_FILE"))"
            
            # Проверяем доступность порта
            if curl -s "http://localhost:$SERVICE_PORT/health" > /dev/null; then
                echo "🌐 Сервис доступен на http://localhost:$SERVICE_PORT"
            else
                echo "⚠️  Сервис запущен, но порт $SERVICE_PORT недоступен"
            fi
        else
            echo "❌ Ошибка запуска $SERVICE_NAME"
            rm -f "$PID_FILE"
            exit 1
        fi
        ;;
        
    stop)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "🛑 Остановка $SERVICE_NAME (PID: $PID)..."
                kill "$PID"
                
                # Ждем завершения
                for i in {1..10}; do
                    if ! ps -p "$PID" > /dev/null 2>&1; then
                        break
                    fi
                    sleep 1
                done
                
                # Принудительная остановка если не завершился
                if ps -p "$PID" > /dev/null 2>&1; then
                    echo "⚡ Принудительная остановка $SERVICE_NAME..."
                    kill -9 "$PID"
                fi
                
                rm -f "$PID_FILE"
                echo "✅ $SERVICE_NAME остановлен"
            else
                echo "⚠️  $SERVICE_NAME не запущен"
                rm -f "$PID_FILE"
            fi
        else
            echo "⚠️  $SERVICE_NAME не запущен (PID файл не найден)"
        fi
        ;;
        
    restart)
        echo "🔄 Перезапуск $SERVICE_NAME..."
        $0 stop
        sleep 2
        $0 start
        ;;
        
    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "✅ $SERVICE_NAME запущен (PID: $PID)"
                
                # Проверяем доступность
                if curl -s "http://localhost:$SERVICE_PORT/health" > /dev/null; then
                    echo "🌐 Сервис доступен на http://localhost:$SERVICE_PORT"
                    
                    # Показываем статистику
                    echo "📊 Статистика сервиса:"
                    curl -s "http://localhost:$SERVICE_PORT/stats" | python3 -m json.tool 2>/dev/null || echo "Не удалось получить статистику"
                else
                    echo "❌ Сервис недоступен на порту $SERVICE_PORT"
                fi
            else
                echo "❌ $SERVICE_NAME не запущен (процесс не найден)"
                rm -f "$PID_FILE"
            fi
        else
            echo "❌ $SERVICE_NAME не запущен (PID файл не найден)"
        fi
        ;;
        
    logs)
        if [ -f "$LOG_FILE" ]; then
            echo "📋 Последние логи $SERVICE_NAME:"
            tail -n 50 "$LOG_FILE"
        else
            echo "⚠️  Лог файл $LOG_FILE не найден"
        fi
        ;;
        
    health)
        if curl -s "http://localhost:$SERVICE_PORT/health" > /dev/null; then
            echo "✅ $SERVICE_NAME здоров"
            curl -s "http://localhost:$SERVICE_PORT/health" | python3 -m json.tool
        else
            echo "❌ $SERVICE_NAME недоступен"
            exit 1
        fi
        ;;
        
    *)
        echo "Использование: $0 {start|stop|restart|status|logs|health}"
        echo ""
        echo "Команды:"
        echo "  start   - Запуск сервиса $SERVICE_NAME"
        echo "  stop    - Остановка сервиса $SERVICE_NAME"
        echo "  restart - Перезапуск сервиса $SERVICE_NAME"
        echo "  status  - Статус сервиса $SERVICE_NAME"
        echo "  logs    - Показать логи сервиса"
        echo "  health  - Проверка здоровья сервиса"
        exit 1
        ;;
esac
