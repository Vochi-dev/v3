#!/usr/bin/env bash
# asterisk.sh — управление uvicorn для Asterisk Call Management сервиса: start|stop|restart
set -euo pipefail

APP_MODULE="asterisk:app"
HOST="0.0.0.0"
PORT="8018"
PID_FILE=".uvicorn_asterisk.pid"
LOG_FILE="asterisk_service.log"

case "${1:-start}" in
  start)
    cd "$(dirname "$0")"
    echo "🚀 Запускаем uvicorn для Asterisk Call Management сервиса..."
    # запускаем в отдельной сессии, чтобы потом убить всю группу
    setsid uvicorn "$APP_MODULE" \
      --host "$HOST" \
      --port "$PORT" \
      --reload \
      --log-level info \
      --log-config log_config.json >> "$LOG_FILE" 2>&1 &

    UVICORN_PID=$!
    echo "$UVICORN_PID" > "$PID_FILE"
    echo "✅ Asterisk Call Management сервис запущен на порту $PORT (PID=${UVICORN_PID})"
    ;;

  stop)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]]; then
      PID=$(<"$PID_FILE")
      echo "🛑 Останавливаем Asterisk Call Management сервис (PID=${PID}) и его группу..."
      # Убиваем всю группу процессов, лидером которой является наш PID
      kill -TERM -"$PID" || true 
      rm -f "$PID_FILE"
      echo "✅ Группа процессов Asterisk Call Management сервиса (PID=${PID}) остановлена"
    else
      # fallback: ищем по pgrep
      PID=$(pgrep -f "uvicorn $APP_MODULE --host $HOST --port $PORT" | head -n1 || true)
      if [[ -n "$PID" ]]; then
        echo "🛑 Файла $PID_FILE нет — убиваем по найденному PID=${PID}"
        PGID=$(ps -o pgid= "$PID" | tr -d ' ')
        if [[ -n "$PGID" ]]; then
            kill -TERM -"$PGID" || true
            echo "✅ Группа процессов Asterisk Call Management сервиса (PGID=${PGID}) остановлена"
        else
            kill -TERM "$PID" || true
            echo "✅ Процесс Asterisk Call Management сервиса (PID=${PID}) остановлен (PGID не найден)"
        fi
      else
        echo "⚠️  Процесс Asterisk Call Management сервиса не найден"
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
    echo "🔄 Перезапуск Asterisk Call Management сервиса..."
    "$0" stop || true  # Игнорируем ошибку если процесс уже остановлен
    sleep 2
    "$0" start
    ;;

  status)
    cd "$(dirname "$0")"
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
      PID=$(<"$PID_FILE")
      echo "✅ Asterisk Call Management сервис работает (PID=${PID}, порт $PORT)"
      # Проверка доступности по HTTP
      if curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
        echo "🌐 HTTP эндпоинт доступен"
        echo "📡 API endpoint: http://localhost:$PORT/api/makecallexternal"
      else
        echo "⚠️  HTTP эндпоинт недоступен"
      fi
    else
      echo "❌ Asterisk Call Management сервис не запущен"
      [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
    fi
    ;;

  logs)
    cd "$(dirname "$0")"
    if [[ -f "$LOG_FILE" ]]; then
      echo "📋 Последние логи Asterisk Call Management сервиса:"
      tail -f "$LOG_FILE"
    else
      echo "❌ Файл логов $LOG_FILE не найден"
    fi
    ;;

  test)
    echo "🧪 Тестирование Asterisk Call Management сервиса..."
    echo "📊 Health check:"
    if curl -s "http://localhost:$PORT/health" | python3 -m json.tool; then
      echo "✅ Health check прошел успешно"
    else
      echo "❌ Health check не прошел"
    fi
    
    echo ""
    echo "📊 API Status:"
    if curl -s "http://localhost:$PORT/api/status" | python3 -m json.tool; then
      echo "✅ API Status проверен успешно"
    else
      echo "❌ API Status недоступен"
    fi
    ;;

  *)
    echo "Использование: $0 {start|stop|restart|status|logs|test}"
    echo ""
    echo "Команды:"
    echo "  start   - Запустить Asterisk Call Management сервис"
    echo "  stop    - Остановить Asterisk Call Management сервис"
    echo "  restart - Перезапустить Asterisk Call Management сервис"
    echo "  status  - Проверить статус сервиса"
    echo "  logs    - Показать логи сервиса"
    echo "  test    - Протестировать сервис"
    echo ""
    echo "API Endpoint:"
    echo "  GET /api/makecallexternal?code=150&phone=+375296254070&clientId=SECRET"
    exit 1
    ;;
esac