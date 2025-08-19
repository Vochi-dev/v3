#!/bin/bash
set -e
CFG1=/etc/asterisk/extensions_custom.conf
CFG2=/etc/asterisk/extensions_customs.conf
TARGET=""
if grep -qi 'extensions_customs.conf' /etc/asterisk/extensions.conf 2>/dev/null; then TARGET="$CFG2"; fi
if [ -z "$TARGET" ] && grep -qi 'extensions_custom.conf' /etc/asterisk/extensions.conf 2>/dev/null; then TARGET="$CFG1"; fi
[ -z "$TARGET" ] && [ -f "$CFG2" ] && TARGET="$CFG2"
[ -z "$TARGET" ] && [ -f "$CFG1" ] && TARGET="$CFG1"
if [ -z "$TARGET" ]; then echo "No custom target file found"; exit 1; fi
echo "TARGET=$TARGET"
TS=$(date +%s)
cp -n "$TARGET" "$TARGET.bak_$TS" 2>/dev/null || true
cat > /tmp/_web_originate.conf <<'EOF'
[set-extcall-callerid]
exten => _X.,1,NoOp(SET EXT CALLERID for ${CHANNEL} ext ${EXTEN})
 same => n,Set(_EXTCALL=${DB(extcall/nextcall_${EXTEN})})
 same => n,ExecIf($[${LEN(${EXTCALL})} > 0]?Set(CALLERID(num)=${EXTCALL}))
 same => n,ExecIf($[${LEN(${EXTCALL})} > 0]?Set(CALLERID(name)=Call ${EXTCALL}))
 same => n,ExecIf($[${LEN(${EXTCALL})} > 0]?Set(DB_DELETE(extcall/nextcall_${EXTEN})=1))
 same => n,Return()

[web-originate]
exten => _X.,1,GoSub(set-extcall-callerid,${EXTEN},1)
 same => n,Goto(inoffice,${EXTEN},1)
EOF
if ! grep -q '^\[set-extcall-callerid\]' "$TARGET"; then
  printf "\n" >> "$TARGET"
  cat /tmp/_web_originate.conf >> "$TARGET"
fi
asterisk -rx "dialplan reload"
asterisk -rx "dialplan show web-originate" | head -n 60
