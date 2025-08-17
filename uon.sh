#!/usr/bin/env bash
# uon.sh — сервис интеграции U‑ON (порт 8022)
set -euo pipefail

NAME=uon
PORT=8022
PID_FILE=.uvicorn_${NAME}.pid
LOG_DIR=logs
LOG_FILE=${LOG_DIR}/${NAME}.log

mkdir -p "$LOG_DIR"

start() {
  stop || true
  echo "▶ Запуск ${NAME} на порту ${PORT}..."
  nohup uvicorn uon:app --host 0.0.0.0 --port ${PORT} > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 2
  if netstat -tlnp | grep -q ":${PORT}"; then
    echo "✅ ${NAME} запущен (PID=$(cat "$PID_FILE"))"
  else
    echo "❌ Не удалось запустить ${NAME}"
    exit 1
  fi
}

stop() {
  if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
      echo "▶ Останавливаю ${NAME} (PID=${PID})..."
      kill "$PID" || true
      sleep 1
    fi
    rm -f "$PID_FILE"
    echo "✅ ${NAME} остановлен"
  else
    pkill -f "uvicorn.*uon:app" || true
  fi
}

status() {
  if netstat -tlnp | grep -q ":${PORT}"; then
    echo "✅ ${NAME} слушает порт ${PORT}"
  else
    echo "❌ ${NAME} не запущен"
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop || true; start ;;
  status) status ;;
  *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac


