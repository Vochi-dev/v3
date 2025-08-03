#!/usr/bin/env bash
# desk.sh — управление uvicorn для Desk сервиса: start|stop|restart
set -euo pipefail

APP_MODULE="desk:app"
HOST="0.0.0.0"
PORT="8011"
PID_FILE=".uvicorn_desk.pid"
LOG_FILE="desk_service.log"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "🚀 Запускаем uvicorn для Desk сервиса..."
    # запускаем в отдельной сессии, чтобы потом убить всю группу
    setsid uvicorn "$APP_MODULE" \
      --host "$HOST" \
      --port "$PORT" \
      --log-level info \
      --log-config log_config.json >> "$LOG_FILE" 2>&1 &

    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    echo "✅ Desk сервис запущен на порту $PORT (PID=${UVICORN_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем Desk сервис (PID=${PID}) и его группу..."
      # Убиваем всю группу процессов, лидером которой является наш PID
      kill -TERM -"$PID" || true 
      rm -f "$PID_FILE"
      echo "✅ Группа процессов Desk сервиса (PID=${PID}) остановлена"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        if [[ -n "$PGID" ]]; then
            kill -TERM -"$PGID" || true
            echo "✅ Группа процессов Desk сервиса (PGID=${PGID}) остановлена"
        else
            kill -TERM "$PID" || true
            echo "✅ Процесс Desk сервиса (PID=${PID}) остановлен (PGID не найден)"
        fi
      else
        echo "⚠️  Процесс Desk сервиса не найден"
      fi
    fi
    
    # Дополнительная очистка порта
    echo "🧹 Чистим порт $PORT..."
    netstat -tlnp 2>/dev/null | grep ":$PORT " | awk '{print $7}' | cut -d'/' -f1 | while read pid; do
      if [[ -n "$pid" ]]; then
        echo "$PORT/tcp:            $pid"
        kill -TERM "$pid" || true
      fi
    done
    echo "✅ Порт $PORT свободен"
    ;;

  restart)
    echo "🔄 Перезапуск Desk сервиса..."
    "$0" stop || true  # Игнорируем ошибку если процесс уже остановлен
    sleep 2
    "$0" start
    ;;

  status)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
      PID=$(<"$PID_FILE")
      echo "✅ Desk сервис работает (PID=${PID}, порт $PORT)"
      # Проверка доступности по HTTP
      if curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
        echo "🌐 HTTP эндпоинт доступен"
      else
        echo "⚠️  HTTP эндпоинт недоступен"
      fi
    else
      echo "❌ Desk сервис не запущен"
      [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
    fi
    ;;

  logs)
    cd "$(dirname "$0")"
    if [[ -f "$LOG_FILE" ]]; then
      echo "📋 Последние логи Desk сервиса:"
      tail -f "$LOG_FILE"
    else
      echo "❌ Файл логов $LOG_FILE не найден"
    fi
    ;;

  test)
    echo "🧪 Тестирование Desk сервиса..."
    if curl -s "http://localhost:$PORT/" | python3 -m json.tool; then
      echo "✅ Тест прошел успешно"
    else
      echo "❌ Тест не прошел"
    fi
    ;;

  *)
    echo "Использование: $0 {start|stop|restart|status|logs|test}"
    echo ""
    echo "Команды:"
    echo "  start   - Запустить Desk сервис"
    echo "  stop    - Остановить Desk сервис"
    echo "  restart - Перезапустить Desk сервис"
    echo "  status  - Проверить статус сервиса"
    echo "  logs    - Показать логи сервиса"
    echo "  test    - Протестировать сервис"
    exit 1
    ;;
esac 