#!/usr/bin/env bash
# dial.sh — управление uvicorn для сервиса схем: start|stop|restart
set -euo pipefail

APP_MODULE="dial_service:app"
HOST="0.0.0.0"
PORT="8005"
PID_FILE=".uvicorn_dial.pid"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "🚀 Запускаем uvicorn для сервиса схем..."
    # запускаем в отдельной сессии, чтобы потом убить всю группу
    setsid uvicorn "$APP_MODULE" \
      --host "$HOST" \
      --port "$PORT" \
      --reload \
      --log-level debug \
      --log-config log_config.json &

    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    echo "✅ Сервис схем запущен на порту $PORT (PID=${UVICORN_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем сервис схем (PID=${PID}) и его группу..."
      # Убиваем всю группу процессов, лидером которой является наш PID
      kill -TERM -"$PID" || true 
      rm -f "$PID_FILE"
      echo "✅ Группа процессов сервиса схем (PID=${PID}) остановлена"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        if [[ -n "$PGID" ]]; then
            kill -TERM -"$PGID" || true
            echo "✅ Группа процессов сервиса схем (PGID=${PGID}) остановлена"
        else
            kill -TERM "$PID" || true
            echo "✅ Процесс сервиса схем (PID=${PID}) остановлен (PGID не найден)"
        fi
      else
        echo "⚠️  Процесс сервиса схем не найден"
      fi
    fi

    # Принудительно очищаем порт
    echo "🧹 Чистим порт $PORT..."
    if command -v fuser &>/dev/null; then
      fuser -k "$PORT"/tcp || true
    elif command -v lsof &>/dev/null; then
      lsof -ti:"$PORT" | xargs -r kill -9 || true
    else
        echo "⚠️  Команды fuser и lsof не найдены. Невозможно принудительно очистить порт."
    fi
    echo "✅ Порт $PORT свободен"
    exit 0
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  *)
    echo "Использование: $0 {start|stop|restart}"
    exit 1
    ;;
esac 