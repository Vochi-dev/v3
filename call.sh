#!/usr/bin/env bash
# call.sh — управление uvicorn для сервиса записей разговоров: start|stop|restart
set -euo pipefail

APP_MODULE="call_download:app"
HOST="0.0.0.0"
PORT="8012"
PID_FILE=".uvicorn_call.pid"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "🚀 Запускаем uvicorn для сервиса записей разговоров..."
    # запускаем в отдельной сессии, чтобы потом убить всю группу
    setsid uvicorn "$APP_MODULE" \
      --host "$HOST" \
      --port "$PORT" \
      --log-level debug \
      --log-config log_config.json &

    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    echo "✅ Сервис записей разговоров запущен на порту $PORT (PID=${UVICORN_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем сервис записей разговоров (PID=${PID}) и его группу..."
      # Убиваем всю группу процессов, лидером которой является наш PID
      kill -TERM -"$PID" || true 
      rm -f "$PID_FILE"
      echo "✅ Группа процессов сервиса записей разговоров (PID=${PID}) остановлена"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        if [[ -n "$PGID" ]]; then
            kill -TERM -"$PGID" || true
            echo "✅ Группа процессов сервиса записей разговоров (PGID=${PGID}) остановлена"
        else
            kill -TERM "$PID" || true
            echo "✅ Процесс сервиса записей разговоров (PID=${PID}) остановлен (PGID не найден)"
        fi
      else
        echo "⚠️  Процесс сервиса записей разговоров не найден"
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
        echo "✅ Сервис записей разговоров работает (PID=${PID}) на порту $PORT"
        # Проверяем что порт действительно слушается
        if netstat -tlnp | grep -q ":$PORT"; then
          echo "📡 Порт $PORT активен"
        else
          echo "⚠️  PID найден, но порт $PORT не слушается"
        fi
      else
        echo "❌ PID файл есть, но процесс не найден (PID=${PID})"
        rm -f "$PID_FILE"
      fi
    else
      echo "❌ Сервис записей разговоров не запущен (PID файл отсутствует)"
    fi
    ;;

  *)
    echo "Использование: $0 {start|stop|restart|status}"
    echo ""
    echo "Управление сервисом записей разговоров (call_download.py)"
    echo "Порт: $PORT"
    echo "Модуль: $APP_MODULE"
    echo ""
    echo "Доступные API endpoints:"
    echo "  GET  http://localhost:$PORT/               - Информация о сервисе"
    echo "  GET  http://localhost:$PORT/health         - Проверка состояния"
    echo "  GET  http://localhost:$PORT/recordings/stats - Статистика хранилища"
    echo "  POST http://localhost:$PORT/recordings/search - Поиск записей"
    echo "  GET  http://localhost:$PORT/recordings/download/{enterprise}/{call_id} - Скачивание"
    echo "  POST http://localhost:$PORT/recordings/upload - Загрузка записи"
    echo "  DELETE http://localhost:$PORT/recordings/cleanup - Очистка старых записей"
    exit 1
    ;;
esac 