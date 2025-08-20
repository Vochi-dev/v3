#!/bin/bash
set -e
LOGDIR=/root/asterisk-webhook/logs/remote
TMP=/root/asterisk-webhook/tmp
mkdir -p "$LOGDIR" "$TMP"
# 1) Забираем актуальный файл
sshpass -p '5atx9Ate@pbx' scp -P 5059 -o StrictHostKeyChecking=no root@10.88.10.19:/etc/asterisk/extensions_custom.conf "$TMP/ext_custom.remote" > "$LOGDIR/_pull_ext_custom.log" 2>&1 || true
# 2) Готовим новый файл: вырезаем старый блок и добавляем наш
awk 'BEGIN{skip=0} /^\[set-extcall-callerid\]/{skip=1; next} skip && /^\[/{skip=0} !skip{print}' "$TMP/ext_custom.remote" > "$TMP/ext_custom.new" || true
printf '\n' >> "$TMP/ext_custom.new"
cat "/root/asterisk-webhook/tmp/_set_extcallerid.conf" >> "$TMP/ext_custom.new"
# 3) Копируем на удалённый и применяем с бэкапом и reload
sshpass -p '5atx9Ate@pbx' scp -P 5059 -o StrictHostKeyChecking=no "$TMP/ext_custom.new" root@10.88.10.19:/tmp/extensions_custom.conf.new > "$LOGDIR/_push_ext_custom.log" 2>&1
sshpass -p '5atx9Ate@pbx' ssh -p 5059 -o StrictHostKeyChecking=no root@10.88.10.19 'bash -lc '"'"'set -e; T=/etc/asterisk/extensions_custom.conf; TS=$(date +%s); [ -f "$T" ] && cp -a "$T" "$T.bak_$TS" 2>/dev/null || true; mv /tmp/extensions_custom.conf.new "$T"; asterisk -rx "dialplan reload"; echo "=== STAT ==="; ls -l --full-time "$T"; echo "=== DIALPLAN ==="; asterisk -rx "dialplan show set-extcall-callerid" | sed -n "1,80p"'"'"' > "$LOGDIR/_apply_ext_custom.log" 2>&1
