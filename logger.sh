#!/bin/bash

# Call Logger Service Management Script
# Порт: 8026
# Сервис: logger.py

SERVICE_NAME="logger"
SERVICE_FILE="logger.py"
SERVICE_PORT=8026
PID_FILE="pids/logger.pid"
LOG_FILE="logs/logger.log"

# Создаем директории если их нет
mkdir -p pids
mkdir -p logs

case "$1" in
    start)
        echo "Starting $SERVICE_NAME service..."
        
        # Проверяем, не запущен ли уже сервис
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p $PID > /dev/null 2>&1; then
                echo "$SERVICE_NAME is already running (PID: $PID)"
                exit 1
            else
                echo "Removing stale PID file..."
                rm -f "$PID_FILE"
            fi
        fi
        
        # Проверяем, не занят ли порт
        if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "Port $SERVICE_PORT is already in use!"
            exit 1
        fi
        
        # Запускаем сервис (setsid отвязывает от терминала)
        setsid nohup /usr/bin/python3 /usr/local/bin/uvicorn $SERVICE_NAME:app \
            --host 0.0.0.0 \
            --port $SERVICE_PORT \
            --workers 2 \
            --log-level info \
            --log-config log_config.json \
            > "$LOG_FILE" 2>&1 &
        
        echo $! > "$PID_FILE"
        
        # Ждем немного и проверяем запуск
        sleep 2
        if ps -p $(cat "$PID_FILE") > /dev/null 2>&1; then
            echo "$SERVICE_NAME started successfully (PID: $(cat "$PID_FILE"))"
            echo "Log file: $LOG_FILE"
            echo "Health check: curl http://localhost:$SERVICE_PORT/health"
        else
            echo "Failed to start $SERVICE_NAME"
            rm -f "$PID_FILE"
            exit 1
        fi
        ;;
        
    stop)
        echo "Stopping $SERVICE_NAME service..."
        
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p $PID > /dev/null 2>&1; then
                kill $PID
                
                # Ждем завершения процесса
                for i in {1..10}; do
                    if ! ps -p $PID > /dev/null 2>&1; then
                        break
                    fi
                    sleep 1
                done
                
                # Если процесс все еще работает, убиваем принудительно
                if ps -p $PID > /dev/null 2>&1; then
                    echo "Force killing $SERVICE_NAME..."
                    kill -9 $PID
                fi
                
                rm -f "$PID_FILE"
                echo "$SERVICE_NAME stopped"
            else
                echo "$SERVICE_NAME is not running"
                rm -f "$PID_FILE"
            fi
        else
            echo "$SERVICE_NAME is not running (no PID file)"
            
            # Проверяем процессы на всякий случай
            RUNNING_PID=$(lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t 2>/dev/null)
            if [ ! -z "$RUNNING_PID" ]; then
                echo "Found process on port $SERVICE_PORT (PID: $RUNNING_PID), killing..."
                kill $RUNNING_PID
            fi
        fi
        ;;
        
    restart)
        echo "Restarting $SERVICE_NAME service..."
        $0 stop
        sleep 2
        $0 start
        ;;
        
    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p $PID > /dev/null 2>&1; then
                echo "$SERVICE_NAME is running (PID: $PID)"
                
                # Проверяем порт
                if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
                    echo "Port $SERVICE_PORT is active"
                    
                    # Проверяем health endpoint
                    if command -v curl >/dev/null 2>&1; then
                        echo "Health check:"
                        curl -s http://localhost:$SERVICE_PORT/health 2>/dev/null || echo "Health endpoint not responding"
                    fi
                else
                    echo "Warning: Port $SERVICE_PORT is not active"
                fi
            else
                echo "$SERVICE_NAME is not running (stale PID file)"
                rm -f "$PID_FILE"
            fi
        else
            echo "$SERVICE_NAME is not running"
        fi
        ;;
        
    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "Log file not found: $LOG_FILE"
        fi
        ;;
        
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "Commands:"
        echo "  start   - Start the $SERVICE_NAME service"
        echo "  stop    - Stop the $SERVICE_NAME service"
        echo "  restart - Restart the $SERVICE_NAME service"
        echo "  status  - Show service status"
        echo "  logs    - Show service logs (tail -f)"
        echo ""
        echo "Service: $SERVICE_NAME ($SERVICE_FILE)"
        echo "Port: $SERVICE_PORT"
        echo "PID file: $PID_FILE"
        echo "Log file: $LOG_FILE"
        exit 1
        ;;
esac

exit 0
