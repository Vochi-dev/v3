#!/usr/bin/env bash
# bot.sh — управление Telegram-ботами: start|stop|restart|status
set -euo pipefail

BOT_SCRIPT="/root/asterisk-webhook/start_bots.sh"
LOG_FILE="/root/asterisk-webhook/bots.log"

case "${1:-start}" in
  start)
    echo "🚀 Запускаем Telegram-боты..."
    
    # Проверяем что telegram_auth_service запущен
    if ! curl -s http://localhost:8016/ > /dev/null 2>&1; then
      echo "⚠️  Telegram Auth сервис (порт 8016) недоступен"
      echo "   Запустите его командой: ./telegram.sh start"
      exit 1
    fi
    
    # Запускаем боты через start_bots.sh
    if bash "$BOT_SCRIPT"; then
      sleep 3
      BOT_COUNT=$(ps aux | grep "app/telegram/bot.py" | grep -v grep | wc -l)
      EXPECTED_COUNT=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != '';" 2>/dev/null | xargs || echo "?")
      echo "✅ Telegram-боты запущены: $BOT_COUNT из $EXPECTED_COUNT"
    else
      echo "❌ Ошибка запуска Telegram-ботов"
      exit 1
    fi
    ;;

  stop)
    echo "🛑 Останавливаем Telegram-боты..."
    pkill -f "app/telegram/bot.py" || true
    sleep 2
    
    # Проверяем что все остановлены
    BOT_COUNT=$(ps aux | grep "app/telegram/bot.py" | grep -v grep | wc -l || echo "0")
    BOT_COUNT=${BOT_COUNT:-0}
    if [ "$BOT_COUNT" -eq 0 ]; then
      echo "✅ Все Telegram-боты остановлены"
    else
      echo "⚠️  Остались запущенные боты: $BOT_COUNT"
      echo "   Принудительно останавливаем..."
      pkill -9 -f "app/telegram/bot.py" || true
      sleep 1
      echo "✅ Все боты остановлены принудительно"
    fi
    ;;

  restart)
    echo "🔄 Перезапуск Telegram-ботов..."
    "$0" stop
    sleep 2
    "$0" start
    ;;

  status)
    echo "📊 Статус Telegram-ботов:"
    
    # Проверяем telegram_auth_service
    if curl -s http://localhost:8016/ > /dev/null 2>&1; then
      echo "   ✅ Telegram Auth сервис (порт 8016) работает"
    else
      echo "   ❌ Telegram Auth сервис (порт 8016) недоступен"
    fi
    
    # Считаем количество запущенных ботов
    BOT_COUNT=$(ps aux | grep "app/telegram/bot.py" | grep -v grep | wc -l || echo "0")
    EXPECTED_COUNT=$(PGPASSWORD='r/Yskqh/ZbZuvjb2b3ahfg==' psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM enterprises WHERE bot_token IS NOT NULL AND bot_token != '';" 2>/dev/null | xargs || echo "?")
    
    echo "   📊 Запущено ботов: $BOT_COUNT из $EXPECTED_COUNT"
    
    if [[ "$BOT_COUNT" -gt 0 ]]; then
      echo "   ✅ Telegram-боты работают"
      
      # Показываем список предприятий с ботами
      echo ""
      echo "   Список запущенных ботов:"
      ps aux | grep "app/telegram/bot.py" | grep -v grep | awk '{for(i=11;i<=NF;i++) printf "%s ", $i; print ""}' | grep -oP 'enterprise \K\d+' | sort | while read -r ent; do
        echo "      • Предприятие $ent"
      done
    else
      echo "   ❌ Telegram-боты не запущены"
    fi
    
    # Проверяем логи
    if [[ -f "$LOG_FILE" ]]; then
      echo ""
      echo "   📋 Последние 5 строк лога:"
      tail -5 "$LOG_FILE" | sed 's/^/      /'
    fi
    ;;

  logs)
    echo "📋 Логи Telegram-ботов:"
    if [[ -f "$LOG_FILE" ]]; then
      tail -f "$LOG_FILE"
    else
      echo "❌ Файл логов $LOG_FILE не найден"
    fi
    ;;

  *)
    echo "Использование: $0 {start|stop|restart|status|logs}"
    echo ""
    echo "Команды:"
    echo "  start   - Запустить все Telegram-боты"
    echo "  stop    - Остановить все Telegram-боты"
    echo "  restart - Перезапустить все Telegram-боты"
    echo "  status  - Показать статус Telegram-ботов"
    echo "  logs    - Показать логи Telegram-ботов (tail -f)"
    exit 1
    ;;
esac

