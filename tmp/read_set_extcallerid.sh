#!/bin/bash
set -e
TARGET=""
if grep -qi "extensions_customs.conf" /etc/asterisk/extensions.conf; then TARGET=/etc/asterisk/extensions_customs.conf; fi
if grep -qi "extensions_custom.conf" /etc/asterisk/extensions.conf; then TARGET=/etc/asterisk/extensions_custom.conf; fi
[ -z "$TARGET" ] && [ -f /etc/asterisk/extensions_custom.conf ] && TARGET=/etc/asterisk/extensions_custom.conf
[ -z "$TARGET" ] && [ -f /etc/asterisk/extensions_customs.conf ] && TARGET=/etc/asterisk/extensions_customs.conf
echo "TARGET=$TARGET"
echo "=== BLOCK ==="
awk '/^\[set-extcall-callerid\]/{f=1;print;next} /^\[.*\]/{if(f){exit}} f{print}' "$TARGET"
echo "=== DIALPLAN ==="
asterisk -rx "dialplan show set-extcall-callerid" | sed -n '1,160p'
