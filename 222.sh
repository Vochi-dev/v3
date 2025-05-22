#!/bin/bash

BOT_TOKEN="8133181812:AAH_Ty_ndTeO8Y_NlTEFkbBsgGIrGUlH5I0"
TEXT="Тест от консолі для юніта 0201"

# Список chat_id, которые у вас привязаны к этому bot_token
CHAT_IDS=(374573193 989104050)

for CHAT in "${CHAT_IDS[@]}"; do
  echo "Отправляем в $CHAT…"
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="$CHAT" \
    -d text="$TEXT" \
    && echo "OK" || echo "FAIL"
done
