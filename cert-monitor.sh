#!/bin/bash
# SSL Certificate Monitor Script
# Проверяет срок действия SSL сертификата и отправляет предупреждения

DOMAIN="bot.vochi.by"
DAYS_WARNING=30
LOG_FILE="/var/log/cert-monitor.log"

# Функция логирования
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> $LOG_FILE
}

# Получить дату истечения сертификата
EXPIRY_DATE=$(openssl s_client -connect $DOMAIN:443 -servername $DOMAIN </dev/null 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)

if [ -z "$EXPIRY_DATE" ]; then
    log "ERROR: Не удалось получить информацию о сертификате для $DOMAIN"
    exit 1
fi

EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s)
CURRENT_EPOCH=$(date +%s)
DAYS_LEFT=$(( ($EXPIRY_EPOCH - $CURRENT_EPOCH) / 86400 ))

log "INFO: Сертификат $DOMAIN истекает $EXPIRY_DATE, осталось $DAYS_LEFT дней"

# Проверка критических сроков
if [ $DAYS_LEFT -le 7 ]; then
    # Критично - меньше недели
    log "CRITICAL: SSL сертификат $DOMAIN истекает через $DAYS_LEFT дней!"
    echo "🚨 КРИТИЧНО: SSL сертификат $DOMAIN истекает через $DAYS_LEFT дней!" >> /tmp/cert-alert.txt
    echo "Необходимо немедленное продление!" >> /tmp/cert-alert.txt
    echo "Команда для продления: certbot renew --force-renewal --standalone" >> /tmp/cert-alert.txt
elif [ $DAYS_LEFT -le $DAYS_WARNING ]; then
    # Предупреждение
    log "WARNING: SSL сертификат $DOMAIN истекает через $DAYS_LEFT дней"
    echo "⚠️ ПРЕДУПРЕЖДЕНИЕ: SSL сертификат $DOMAIN истекает через $DAYS_LEFT дней" >> /tmp/cert-alert.txt
    echo "Рекомендуется продлить в ближайшее время" >> /tmp/cert-alert.txt
else
    # Все в порядке
    log "OK: SSL сертификат $DOMAIN в порядке, осталось $DAYS_LEFT дней"
fi

# Обновить информацию в cert.md
if [ -f "/root/asterisk-webhook/cert.md" ]; then
    sed -i "s/- \*\*Остается:\*\* .*$/- **Остается:** ~$DAYS_LEFT дней/" /root/asterisk-webhook/cert.md
    sed -i "s/\*\*Последнее обновление:\*\* .*$/\*\*Последнее обновление:\*\* $(date '+%d.%m.%Y')/" /root/asterisk-webhook/cert.md
fi