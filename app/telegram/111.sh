#!/usr/bin/env bash
# run_uvicorn.sh — управление uvicorn: start|stop
set -euo pipefail

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "Запускаем uvicorn..."
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
    if [[ -f .uvicorn.pid ]]; then
      PID=$(<.uvicorn.pid)
      echo "Останавливаем uvicorn (PID=${PID}) и все его дочерние процессы..."
      # убиваем всю группу процессов
      kill -TERM -"$PID" || true
      rm -f .uvicorn.pid
      echo "✅ uvicorn остановлен"
    else
      echo "⚠️  Файл .uvicorn.pid не найден — ничего не делаем"
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
