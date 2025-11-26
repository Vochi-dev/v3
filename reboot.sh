#!/usr/bin/env bash
# reboot.sh — управление сервисом reboot.py: start|stop|restart
set -euo pipefail

APP="reboot.py"
HOST="0.0.0.0"
PORT="8009"
PID_FILE=".reboot_service.pid"
LOG_FILE="reboot_service.log"
PYTHON_BIN="python3"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      OLD_PID=$(<"$PID_FILE")
      if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "Сервис уже запущен (PID=$OLD_PID)"
        exit 0
      else
        echo "⚠️ PID файл существует, но процесс $OLD_PID не найден. Удаляем старый PID файл."
        rm -f "$PID_FILE"
      fi
    fi
    echo "🚀 Запускаем reboot.py на порту $PORT..."
    setsid nohup $PYTHON_BIN $APP >> "$LOG_FILE" 2>&1 &
    REBOOT_PID=$!
    echo "$REBOOT_PID" > "$PID_FILE"
    echo "✅ reboot.py запущен (PID=${REBOOT_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем reboot.py (PID=${PID})..."
      kill "$PID" || true
      rm -f "$PID_FILE"
      echo "✅ reboot.py остановлен"
    else
      PID=$(pgrep -f "$APP" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        kill "$PID" || true
        echo "✅ reboot.py остановлен (PID=${PID})"
      else
        echo "⚠️  reboot.py не найден"
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