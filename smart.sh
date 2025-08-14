#!/usr/bin/env bash
# smart.sh — управление uvicorn для сервиса smart.py: start|stop|restart|status
set -euo pipefail

APP_MODULE="smart:app"
HOST="0.0.0.0"
PORT="8021"
PID_FILE=".uvicorn_smart.pid"
LOG_FILE="smart_service.log"

start() {
  cd "$(dirname "$0")"
  echo "🚀 Запускаем smart.py на ${PORT}..."
  nohup setsid uvicorn "$APP_MODULE" \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "✅ smart.py запущен (PID=$(<"$PID_FILE"))"
}

stop() {
  cd "$(dirname "$0")"
  if [[ -f "$PID_FILE" ]]; then
    PID=$(<"$PID_FILE")
    echo "🛑 Останавливаем smart.py (PID=${PID}) и его группу..."
    kill -TERM -"$PID" || true
    rm -f "$PID_FILE"
    echo "✅ Остановлен"
  else
    PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
    if [[ -n "$PID" ]]; then
      echo "🛑 Останавливаем по PID=${PID}"
      PGID=$(ps -o pgid= "$PID" | tr -d ' ')
      if [[ -n "$PGID" ]]; then
        kill -TERM -"$PGID" || true
      else
        kill -TERM "$PID" || true
      fi
      echo "✅ Остановлен"
    else
      echo "⚠️  Процесс smart.py не найден"
    fi
  fi
}

status() {
  if netstat -tlnp 2>/dev/null | grep -q ":$PORT"; then
    echo "✅ smart.py слушает порт $PORT"
  else
    echo "❌ smart.py не запущен"
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  *) echo "Использование: $0 {start|stop|restart|status}"; exit 1 ;;
esac




