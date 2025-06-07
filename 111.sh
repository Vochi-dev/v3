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
      --port 8000 \
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
      # Убиваем всю группу процессов, лидером которой является наш PID
      kill -TERM -"$PID" || true 
      rm -f .uvicorn.pid
      echo "✅ uvicorn группа PID=${PID} остановлена"
    else
      # fallback: ищем по pgrep
      # Ищем PID родительского процесса uvicorn, а не дочерних от reloader
      PID=$(pgrep -f "uvicorn main:app --host 0.0.0.0 --port 8000" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла .uvicorn.pid нет — убиваем по найденному PID=${PID}"
        # Получаем PGID (Process Group ID) для этого PID
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        if [[ -n "$PGID" ]]; then
            kill -TERM -"$PGID" || true
            echo "✅ uvicorn группа PGID=${PGID} (для PID=${PID}) остановлена"
        else
            # Если PGID не удалось получить, пробуем убить сам PID
            kill -TERM "$PID" || true
            echo "✅ uvicorn PID=${PID} остановлен (PGID не найден)"
        fi
      else
        echo "⚠️  Процесс uvicorn не найден"
      fi
    fi

    # Принудительно очищаем порт 8000
    echo "🧹 Чистим порт 8000..."
    if command -v fuser &>/dev/null; then
      fuser -k 8000/tcp || true
    elif command -v lsof &>/dev/null; then # Добавлена проверка на lsof, если fuser нет
      lsof -ti:8000 | xargs -r kill -9 || true
    else
        echo "⚠️  Команды fuser и lsof не найдены. Невозможно принудительно очистить порт."
    fi
    echo "✅ Порт 8000 свободен"
    exit 0 # Для команды stop всегда выходим с кодом 0, если дошли сюда
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