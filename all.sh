#!/usr/bin/env bash
# all.sh — управление всеми сервисами: start|stop|restart
# Пересборка фронта + перезапуск всех сервисов системы
set -euo pipefail

# Список всех сервисов в порядке зависимости
SERVICES=("admin" "dial" "111" "plan" "smart" "sms" "sms_send" "send_user_sms" "auth" "telegram" "download" "goip" "desk" "call" "miniapp" "asterisk" "retailcrm" "integration_cache")

case "${1:-restart}" in
  start)
    echo "🚀 Запуск всех сервисов системы..."
    
    # Сборка фронта
    echo "📦 Собираем фронт..."
    if cd dial_frontend; then
      if npm run build; then
        echo "✅ Фронт успешно собран"
      else
        echo "❌ Ошибка сборки фронта"
        exit 1
      fi
      cd ..
    else
      echo "❌ Директория dial_frontend не найдена"
      exit 1
    fi
    
    # Запуск всех сервисов
    echo "🔄 Запускаем все сервисы..."
    for service in "${SERVICES[@]}"; do
      echo "   ▶ Запускаем ${service}.sh..."
      if [[ "$service" == "sms" ]]; then
        # SMS-сервис: сначала останавливаем старый, потом запускаем новый
        pkill -f "goip_sms_service" || true
        pkill -f "deploy.py" || true
        sleep 2
        nohup uvicorn goip_sms_service:app --host 0.0.0.0 --port 8002 > logs/goip_service.log 2>&1 &
        sleep 3
        if netstat -tlnp | grep -q ":8002" && ps aux | grep -q "goip_sms_service" && ! ps aux | grep -q "deploy.py"; then
          echo "   ✅ ${service} запущен"
        else
          echo "   ❌ Ошибка запуска ${service}"
        fi
      else
        if ./${service}.sh start; then
          echo "   ✅ ${service} запущен"
        else
          echo "   ❌ Ошибка запуска ${service}"
        fi
      fi
    done
    
    # Запуск reboot.py (порт 8009)
    echo "   ▶ Запускаем reboot.py (порт 8009)..."
    nohup python3 reboot.py > reboot_service.log 2>&1 &
    sleep 2
    if netstat -tlnp | grep -q ":8009"; then
      echo "   ✅ reboot.py запущен"
    else
      echo "   ❌ Ошибка запуска reboot.py"
    fi
    
    # Запуск ewelink_api.py (порт 8010)
    echo "   ▶ Запускаем ewelink_api.py (порт 8010)..."
    nohup uvicorn ewelink_api:app --host 0.0.0.0 --port 8010 > ewelink_service.log 2>&1 &
    sleep 2
    if netstat -tlnp | grep -q ":8010"; then
      echo "   ✅ ewelink_api.py запущен"
    else
      echo "   ❌ Ошибка запуска ewelink_api.py"
    fi
    
    # Ожидание готовности Telegram Auth сервиса
    echo "   ⏳ Ожидаем готовности Telegram Auth сервиса (порт 8016)..."
    sleep 3
    
    # Проверка доступности сервиса
    max_attempts=30
    attempt=1
    while [ $attempt -le $max_attempts ]; do
      if curl -s http://localhost:8016/ > /dev/null 2>&1; then
        echo "   ✅ Telegram Auth сервис готов"
        break
      else
        echo "   ⏳ Попытка $attempt/$max_attempts - ждем готовности сервиса..."
        sleep 2
        attempt=$((attempt + 1))
      fi
    done
    
    if [ $attempt -gt $max_attempts ]; then
      echo "   ❌ Telegram Auth сервис не готов - пропускаем запуск ботов"
    else
      # Запуск всех Telegram-ботов
      echo "   ▶ Запускаем все Telegram-боты..."
      if ./start_bots.sh; then
        echo "   ✅ Telegram-боты запущены"
        sleep 5
        # Проверяем результат
        BOT_COUNT=$(ps aux | grep "app/telegram/bot.py" | grep -v grep | wc -l)
        EXPECTED_COUNT=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != '';" 2>/dev/null || echo "?")
        echo "   📊 Запущено ботов: $BOT_COUNT из $EXPECTED_COUNT"
      else
        echo "   ❌ Ошибка запуска Telegram-ботов"
      fi
    fi
    
    echo "🎉 Все сервисы запущены!"
    ;;

  stop)
    echo "🛑 Остановка всех сервисов..."
    
    # Сначала останавливаем все Telegram-боты
    echo "   ▶ Останавливаем все Telegram-боты..."
    pkill -f "app.telegram.bot" || true
    sleep 2
    echo "   ✅ Telegram-боты остановлены"
    
    # Останавливаем сервисы в обратном порядке
    for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
      service="${SERVICES[i]}"
      echo "   ▶ Останавливаем ${service}.sh..."
      if ./${service}.sh stop; then
        echo "   ✅ ${service} остановлен"
      else
        echo "   ⚠️  Проблема при остановке ${service}"
      fi
    done
    
    # Остановка reboot.py
    echo "   ▶ Останавливаем reboot.py..."
    pkill -f reboot.py || true
    sleep 1
    
    # Остановка ewelink_api.py
    echo "   ▶ Останавливаем ewelink_api.py..."
    pkill -f 'uvicorn.*ewelink_api' || true
    sleep 1
    
    echo "✅ Все сервисы остановлены"
    ;;

  restart)
    echo "🔄 Перезапуск всех сервисов с пересборкой фронта..."
    
    # Остановка всех сервисов
    echo "🛑 Останавливаем все сервисы..."
    
    # Сначала останавливаем все Telegram-боты
    echo "   ▶ Останавливаем все Telegram-боты..."
    pkill -f "app.telegram.bot" || true
    sleep 2
    echo "   ✅ Telegram-боты остановлены"
    
    for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
      service="${SERVICES[i]}"
      echo "   ▶ Останавливаем ${service}.sh..."
      ./${service}.sh stop || echo "   ⚠️  Проблема при остановке ${service}"
    done
    
    echo "⏳ Пауза 2 секунды..."
    sleep 2
    
    # Сборка фронта
    echo "📦 Пересобираем фронт..."
    if cd dial_frontend; then
      if npm run build; then
        echo "✅ Фронт успешно пересобран"
      else
        echo "❌ Ошибка пересборки фронта"
        cd ..
        exit 1
      fi
      cd ..
    else
      echo "❌ Директория dial_frontend не найдена"
      exit 1
    fi
    
    # Запуск всех сервисов
    echo "🚀 Запускаем все сервисы..."
    for service in "${SERVICES[@]}"; do
      echo "   ▶ Запускаем ${service}.sh..."
      if [[ "$service" == "sms" ]]; then
        # SMS-сервис: сначала останавливаем старый, потом запускаем новый
        pkill -f "goip_sms_service" || true
        pkill -f "deploy.py" || true
        sleep 2
        nohup uvicorn goip_sms_service:app --host 0.0.0.0 --port 8002 > logs/goip_service.log 2>&1 &
        sleep 3
        if netstat -tlnp | grep -q ":8002" && ps aux | grep -q "goip_sms_service" && ! ps aux | grep -q "deploy.py"; then
          echo "   ✅ ${service} запущен"
        else
          echo "   ❌ Ошибка запуска ${service}"
        fi
      else
        if ./${service}.sh start; then
          echo "   ✅ ${service} запущен"
        else
          echo "   ❌ Ошибка запуска ${service}"
        fi
      fi
      sleep 1  # Небольшая пауза между запусками
    done
    
    # Запуск reboot.py (порт 8009)
    echo "   ▶ Запускаем reboot.py (порт 8009)..."
    nohup python3 reboot.py > reboot_service.log 2>&1 &
    sleep 2
    if netstat -tlnp | grep -q ":8009"; then
      echo "   ✅ reboot.py запущен"
    else
      echo "   ❌ Ошибка запуска reboot.py"
    fi
    
    # Запуск ewelink_api.py (порт 8010)
    echo "   ▶ Запускаем ewelink_api.py (порт 8010)..."
    nohup uvicorn ewelink_api:app --host 0.0.0.0 --port 8010 > ewelink_service.log 2>&1 &
    sleep 2
    if netstat -tlnp | grep -q ":8010"; then
      echo "   ✅ ewelink_api.py запущен"
    else
      echo "   ❌ Ошибка запуска ewelink_api.py"
    fi
    
    # Запуск всех Telegram-ботов
    echo "   ▶ Запускаем все Telegram-боты..."
    if ./start_bots.sh; then
      echo "   ✅ Telegram-боты запущены"
    else
      echo "   ❌ Ошибка запуска Telegram-ботов"
    fi
    
    echo "🎉 Все сервисы перезапущены!"
    ;;

  status)
    echo "📊 Статус всех сервисов:"
    echo ""
    
    # Проверка фронта
    echo "📦 Фронт (dial_frontend):"
    if [[ -d "dial_frontend/dist" ]]; then
      DIST_SIZE=$(du -sh dial_frontend/dist 2>/dev/null | cut -f1 || echo "unknown")
      DIST_TIME=$(stat -c %y dial_frontend/dist 2>/dev/null | cut -d' ' -f1-2 || echo "unknown")
      echo "   ✅ Собран (размер: ${DIST_SIZE}, время: ${DIST_TIME})"
    else
      echo "   ❌ Не собран (dist/ отсутствует)"
    fi
    echo ""
    
    # Проверка всех сервисов
    for service in "${SERVICES[@]}"; do
      echo "🔍 ${service}:"
      if [[ -f "./${service}.sh" ]]; then
        # Проверяем поддерживает ли скрипт команду status
        if ./${service}.sh status >/dev/null 2>&1; then
          ./${service}.sh status
          echo "   (статус выше)"
        else
          # Если status не поддерживается, проверяем наличие PID файла
          PID_FILE=".uvicorn_${service}.pid"
          if [[ -f "$PID_FILE" ]]; then
            PID=$(<"$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
              echo "   ✅ Сервис работает (PID=${PID})"
            else
              echo "   ❌ PID файл есть, но процесс не найден"
            fi
          else
            echo "   ❓ Команда status не поддерживается, PID файл не найден"
          fi
        fi
      else
        echo "   ❌ Скрипт ${service}.sh не найден"
      fi
      echo ""
    done
    
    # --- Статус reboot.py ---
    echo "🔍 reboot.py (порт 8009):"
    if netstat -tlnp | grep -q ":8009"; then
      echo "   ✅ reboot.py работает"
    else
      echo "   ❌ reboot.py не запущен"
    fi
    # --- Статус ewelink_api.py ---
    echo "🔍 ewelink_api.py (порт 8010):"
    if netstat -tlnp | grep -q ":8010"; then
      echo "   ✅ ewelink_api.py работает"
    else
      echo "   ❌ ewelink_api.py не запущен"
    fi
    
    # --- Статус Telegram-ботов ---
    echo "🔍 Telegram-боты:"
    BOT_COUNT=$(ps aux | grep "app/telegram/bot.py" | grep -v grep | wc -l)
    TOTAL_ENTERPRISES=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != '';" 2>/dev/null || echo "?")
    echo "   📊 Запущено ботов: ${BOT_COUNT} из ${TOTAL_ENTERPRISES}"
    if [[ "$BOT_COUNT" -gt 0 ]]; then
      echo "   ✅ Telegram-боты работают"
    else
      echo "   ❌ Telegram-боты не запущены"
    fi
    ;;

  build)
    echo "📦 Только пересборка фронта..."
    if cd dial_frontend; then
      if npm run build; then
        echo "✅ Фронт успешно пересобран"
      else
        echo "❌ Ошибка пересборки фронта"
        exit 1
      fi
      cd ..
    else
      echo "❌ Директория dial_frontend не найдена"
      exit 1
    fi
    ;;

  *)
    echo "Использование: $0 {start|stop|restart|status|build}"
    echo ""
    echo "Команды:"
    echo "  start   - Собрать фронт и запустить все сервисы"
    echo "  stop    - Остановить все сервисы"
    echo "  restart - Пересобрать фронт и перезапустить все сервисы"
    echo "  status  - Показать статус фронта и всех сервисов"
    echo "  build   - Только пересобрать фронт (без перезапуска сервисов)"
    echo ""
    echo "Сервисы: ${SERVICES[*]}"
    echo "Фронт: dial_frontend (npm run build)"
    echo "Telegram-боты: start_bots.sh (все предприятия)"
    echo ""
    echo "Порты сервисов:"
echo "  111 (main): 8000"
echo "  sms: 8002"
echo "  sms_send: 8013"
echo "  send_user_sms: 8014"
echo "  auth: 8015"
echo "  telegram: 8016"
echo "  admin: 8004"
    echo "  dial: 8005"  
    echo "  plan: 8006"
    echo "  download: 8007"
    echo "  reboot: 8009"
    echo "  ewelink: 8010"
    echo "  call: 8012"
    echo "  miniapp: 8017"
    echo "  asterisk: 8018"
    echo ""
    echo "Telegram-боты: динамическое количество (по числу предприятий)"
    exit 1
    ;;
esac 