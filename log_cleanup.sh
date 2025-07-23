#!/bin/bash

# Скрипт автоматической очистки логов для предотвращения переполнения диска
# Запускать через cron раз в неделю: 0 2 * * 0 /root/asterisk-webhook/log_cleanup.sh

LOG_DIR="/root/asterisk-webhook"
MAX_SIZE="50M"  # Максимальный размер лог-файла
BACKUP_LINES=100  # Количество строк для сохранения

echo "=== $(date) === Начинаем очистку логов ===" >> "$LOG_DIR/log_cleanup.log"

# Функция для очистки лог-файла
cleanup_log() {
    local file="$1"
    if [ -f "$file" ]; then
        # Проверяем размер файла
        local size=$(stat -c%s "$file")
        local max_bytes=$((50*1024*1024))  # 50MB в байтах
        
        if [ $size -gt $max_bytes ]; then
            echo "Очищаем $file (размер: $(du -h "$file" | cut -f1))" >> "$LOG_DIR/log_cleanup.log"
            tail -$BACKUP_LINES "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
            echo "Файл $file сокращен до последних $BACKUP_LINES строк" >> "$LOG_DIR/log_cleanup.log"
        fi
    fi
}

# Очищаем основные лог-файлы
cd "$LOG_DIR"

cleanup_log "debug.log"
cleanup_log "log_action.txt"
cleanup_log "reboot_service.log"
cleanup_log "asterisk_events.log"
cleanup_log "bots.log"
cleanup_log "desk_service.log"
cleanup_log "dial_service.log"
cleanup_log "ewelink_service.log"
cleanup_log "plan_service.log"
cleanup_log "uvicorn.log"
cleanup_log "uvicorn_admin.log"

# Очищаем логи в папке logs/
if [ -d "logs" ]; then
    cleanup_log "logs/app.log"
    cleanup_log "logs/uvicorn.log"
    cleanup_log "logs/access.log"
    cleanup_log "logs/goip_service.log"
    cleanup_log "logs/user_actions.log"
fi

# Удаляем старые файлы ротации (старше 7 дней)
find "$LOG_DIR" -name "*.log.*" -mtime +7 -delete 2>/dev/null

echo "=== $(date) === Очистка логов завершена ===" >> "$LOG_DIR/log_cleanup.log"

# Ограничиваем размер лога самого скрипта
if [ -f "$LOG_DIR/log_cleanup.log" ]; then
    tail -200 "$LOG_DIR/log_cleanup.log" > "$LOG_DIR/log_cleanup.log.tmp" && mv "$LOG_DIR/log_cleanup.log.tmp" "$LOG_DIR/log_cleanup.log"
fi 