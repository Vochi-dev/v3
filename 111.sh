#!/usr/bin/env bash
# 111.sh — управление uvicorn: start|stop|restart
set -euo pipefail

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "🚀 Запускаем uvicorn..."
    # запускаем в отдельной сессии, чтобы потом убить всю группу
    setsid uvicorn main:app \
      --host 0.0.0.0 \
      --port 8001 \
      --reload \
      --log-level debug &

    UVICORN_PID=$!
    echo "$UVICORN_PID" > .uvicorn.pid
    echo "✅ uvicorn запущен (PID=${UVICORN_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f .uvicorn.pid ]]; then
      PID=$(<.uvicorn.pid)
      echo "🛑 Останавливаем uvicorn (PID=${PID}) и его группу..."
      kill -TERM -"$PID" || true
      rm -f .uvicorn.pid
      echo "✅ uvicorn остановлен"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn main:app" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла .uvicorn.pid нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        kill -TERM -"$PGID" || true
        echo "✅ uvicorn остановлен (по PID=${PID})"
      else
        echo "⚠️  Процесс uvicorn не найден"
      fi
    fi
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
