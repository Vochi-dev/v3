#!/bin/bash

# Порт, на котором работает сервис
PORT=8002
# Имя файла Python-приложения (без .py)
APP_MODULE="goip_sms_service"
# Имя объекта FastAPI в приложении
APP_INSTANCE="app"
# Путь к лог-файлу для uvicorn
LOG_FILE="/root/asterisk-webhook/logs/goip_service.log"
# Путь к PID-файлу
PID_FILE="/root/asterisk-webhook/goip_service.pid"

# Создаем директорию для логов, если её нет
mkdir -p /root/asterisk-webhook/logs

# Функция для проверки, запущен ли сервис
is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            return 0 # Запущен
        fi
        # Если PID файл есть, но процесса нет, удалим старый PID файл
        rm -f "$PID_FILE"
    fi
    # Проверка по порту, если PID файла нет или процесс не найден по PID
    if lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null ; then
        return 0 # Запущен (найден по порту)
    fi
    return 1 # Не запущен
}

start() {
    if is_running; then
        echo "GoIP SMS Service appears to be already running."
        echo "If it was started in the background, its logs should be in: $LOG_FILE"
        echo "To run it in the foreground with logs in this terminal, please stop the current instance first:"
        echo "  ./sms.sh stop"
        echo "Then start it again:"
        echo "  ./sms.sh start"
        return 1
    fi
    echo "Starting GoIP SMS Service on port $PORT..."
    
    # Запускаем uvicorn в фоне с setsid для отвязки от терминала
    setsid nohup uvicorn "$APP_MODULE:$APP_INSTANCE" --host 0.0.0.0 --port "$PORT" --workers 1 >> "$LOG_FILE" 2>&1 &
    
    # Сохраняем PID
    echo $! > "$PID_FILE"
    
    sleep 2
    if is_running; then
        echo "GoIP SMS Service started successfully (PID: $(cat $PID_FILE))"
        echo "Logs: $LOG_FILE"
    else
        echo "Failed to start GoIP SMS Service"
        rm -f "$PID_FILE"
    fi
}

stop() {
    echo "Stopping GoIP SMS Service..."
    local KILLED_SOMETHING=0

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            echo "Killing process with PID $PID from $PID_FILE..."
            kill -9 "$PID"
            KILLED_SOMETHING=1
        fi
        rm -f "$PID_FILE"
    fi

    # Дополнительная очистка по порту, если процесс все еще висит
    # или если PID файла не было
    PIDS_ON_PORT=$(lsof -Pi :"$PORT" -sTCP:LISTEN -t)
    if [ -n "$PIDS_ON_PORT" ]; then
        echo "Found processes on port $PORT: $PIDS_ON_PORT. Killing them..."
        # kill -9 может принимать несколько PID
        kill -9 $PIDS_ON_PORT
        KILLED_SOMETHING=1
    fi

    if [ "$KILLED_SOMETHING" -eq 1 ]; then
        echo "GoIP SMS Service stopped."
    else
        echo "GoIP SMS Service was not running."
    fi
}

restart() {
    echo "Restarting GoIP SMS Service..."
    stop
    sleep 2 # Даем время процессам завершиться
    start
}

status() {
    if is_running; then
        if [ -f "$PID_FILE" ]; then
            echo "GoIP SMS Service is running with PID $(cat $PID_FILE)."
        else
            # Если PID файла нет, но процесс найден по порту
            PIDS_ON_PORT=$(lsof -Pi :"$PORT" -sTCP:LISTEN -t)
            echo "GoIP SMS Service is running (found on port $PORT, PIDs: $PIDS_ON_PORT). PID file missing."
        fi
    else
        echo "GoIP SMS Service is not running."
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