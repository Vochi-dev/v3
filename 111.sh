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
      --log-level debug \
      --log-config log_config.json &

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
      echo "✅ uvicorn группа PID=${PID} остановлена"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn main:app" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла .uvicorn.pid нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        kill -TERM -"$PGID" || true
        echo "✅ uvicorn группа PID=${PID} остановлена"
      else
        echo "⚠️  Процесс uvicorn не найден"
      fi
    fi

    # Принудительно очищаем порт 8001
    echo "🧹 Чистим порт 8001..."
    if command -v fuser &>/dev/null; then
      fuser -k 8001/tcp || true
    else
      lsof -ti:8001 | xargs -r kill -9 || true
    fi
    echo "✅ Порт 8001 свободен"
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
