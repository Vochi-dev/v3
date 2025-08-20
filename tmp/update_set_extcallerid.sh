set -e
TARGET=""
if grep -qi "extensions_customs.conf" /etc/asterisk/extensions.conf; then TARGET=/etc/asterisk/extensions_customs.conf; fi
if grep -qi "extensions_custom.conf" /etc/asterisk/extensions.conf; then TARGET=/etc/asterisk/extensions_custom.conf; fi
if [ -z "$TARGET" ] && [ -f /etc/asterisk/extensions_custom.conf ]; then TARGET=/etc/asterisk/extensions_custom.conf; fi
if [ -z "$TARGET" ] && [ -f /etc/asterisk/extensions_customs.conf ]; then TARGET=/etc/asterisk/extensions_customs.conf; fi
if [ -z "$TARGET" ]; then echo "NO_TARGET"; exit 1; fi
TS=$(date +%s)
cp -a "$TARGET" "$TARGET.bak_$TS" 2>/dev/null || true
awk 'BEGIN{s=0} /^\[set-extcall-callerid\]/{s=1;next} s && /^\[/{s=0} !s{print}' "$TARGET" > "$TARGET.tmp"
printf "\n" >> "$TARGET.tmp"
cat /tmp/_set_extcallerid.conf >> "$TARGET.tmp"
mv "$TARGET.tmp" "$TARGET"
asterisk -rx "dialplan reload" || true
echo "=== TARGET=$TARGET ==="
echo "=== BLOCK FROM FILE ==="
awk '/^\[set-extcall-callerid\]/{f=1;print;next} /^\[.*\]/{if(f){exit}} f{print}' "$TARGET"
echo "=== DIALPLAN ==="
asterisk -rx "dialplan show set-extcall-callerid" | sed -n '1,140p' || true
