#!/usr/bin/env bash
# ewelink.sh — управление сервисом ewelink_api.py: start|stop|restart
set -euo pipefail

APP="ewelink_api.py"
HOST="0.0.0.0"
PORT="8010"
PID_FILE=".ewelink_service.pid"
LOG_FILE="ewelink_service.log"
PYTHON_BIN="python3"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      echo "Сервис уже запущен (PID=$(<"$PID_FILE"))"
      exit 0
    fi
    echo "🚀 Запускаем ewelink_api.py на порту $PORT..."
    nohup $PYTHON_BIN $APP >> "$LOG_FILE" 2>&1 &
    EWELINK_PID=$!
    echo "$EWELINK_PID" > "$PID_FILE"
    echo "✅ ewelink_api.py запущен (PID=${EWELINK_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем ewelink_api.py (PID=${PID})..."
      kill "$PID" || true
      rm -f "$PID_FILE"
      echo "✅ ewelink_api.py остановлен"
    else
      PID=$(pgrep -f "$APP" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        kill "$PID" || true
        echo "✅ ewelink_api.py остановлен (PID=${PID})"
      else
        echo "⚠️  ewelink_api.py не найден"
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