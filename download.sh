#!/usr/bin/env bash
# download.sh — управление uvicorn для сервиса синхронизации: start|stop|restart
set -euo pipefail

APP_MODULE="download:app"
HOST="0.0.0.0"
PORT="8007"
PID_FILE=".uvicorn_download.pid"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "🚀 Запускаем uvicorn для сервиса синхронизации..."
    # запускаем в отдельной сессии, чтобы потом убить всю группу
    setsid nohup uvicorn "$APP_MODULE" \
      --host "$HOST" \
      --port "$PORT" \
      --log-level info \
      --log-config log_config.json >> logs/download.log 2>&1 &

    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    echo "✅ Сервис синхронизации запущен на порту $PORT (PID=${UVICORN_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем сервис синхронизации (PID=${PID}) и его группу..."
      # Убиваем всю группу процессов, лидером которой является наш PID
      kill -TERM -"$PID" || true 
      rm -f "$PID_FILE"
      echo "✅ Группа процессов сервиса синхронизации (PID=${PID}) остановлена"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        if [[ -n "$PGID" ]]; then
            kill -TERM -"$PGID" || true
            echo "✅ Группа процессов сервиса синхронизации (PGID=${PGID}) остановлена"
        else
            kill -TERM "$PID" || true
            echo "✅ Процесс сервиса синхронизации (PID=${PID}) остановлен (PGID не найден)"
        fi
      else
        echo "⚠️  Процесс сервиса синхронизации не найден"
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

  status)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      if ps -p "$PID" > /dev/null 2>&1; then
        echo "✅ Сервис синхронизации работает (PID=${PID}) на порту $PORT"
        # Проверяем доступность порта
        if command -v curl &>/dev/null; then
          if curl -s "http://localhost:$PORT/health" > /dev/null; then
            echo "✅ Сервис отвечает на HTTP запросы"
          else
            echo "⚠️  Сервис не отвечает на HTTP запросы"
          fi
        fi
      else
        echo "⚠️  PID файл существует, но процесс не найден"
        rm -f "$PID_FILE"
      fi
    else
      PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "⚠️  Процесс найден (PID=${PID}), но PID файл отсутствует"
      else
        echo "❌ Сервис синхронизации не запущен"
      fi
    fi
    ;;

  *)
    echo "Использование: $0 {start|stop|restart|status}"
    echo ""
    echo "Команды:"
    echo "  start   - Запустить сервис синхронизации"
    echo "  stop    - Остановить сервис синхронизации"
    echo "  restart - Перезапустить сервис синхронизации"
    echo "  status  - Показать статус сервиса"
    echo ""
    echo "Сервис запускается на порту $PORT"
    echo "API документация: http://localhost:$PORT/docs"
    echo "Статус здоровья: http://localhost:$PORT/health"
    exit 1
    ;;
esac 