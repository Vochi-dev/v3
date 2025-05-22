#!/bin/bash

BOT_TOKEN="8133181812:AAH_Ty_ndTeO8Y_NlTEFkbBsgGIrGUlH5I0"
for CHAT_ID in 374573193 989104050; do
  curl -s -X POST https://api.telegram.org/bot${BOT_TOKEN}/sendMessage \
    -d chat_id=${CHAT_ID} \
    -d text="🚀 Тестовое уведомление от юнита 0201" \
    -o /dev/null \
    && echo "✔️ Message sent to ${CHAT_ID}"
done
