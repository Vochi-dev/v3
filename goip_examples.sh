#!/bin/bash

# Примеры использования GoIP Management Service API

BASE_URL="http://127.0.0.1:8008"

echo "=== GoIP Management Service Examples ==="
echo

# 1. Проверка здоровья сервиса
echo "1. Проверка здоровья сервиса:"
curl -s "$BASE_URL/health" | python3 -m json.tool
echo

# 2. Получение списка всех устройств
echo "2. Список всех устройств:"
curl -s "$BASE_URL/devices" | python3 -m json.tool
echo

# 3. Получение только активных устройств
echo "3. Только активные устройства:"
curl -s "$BASE_URL/devices" | jq '.[] | select(.port_scan_status == "active")'
echo

# 4. Информация о конкретном устройстве
echo "4. Информация о Vochi-Main:"
curl -s "$BASE_URL/devices/Vochi-Main" | python3 -m json.tool
echo

# 5. Статус линий устройства
echo "5. Статус линий Vochi-Main (первые 5 линий):"
curl -s "$BASE_URL/devices/Vochi-Main/lines" | jq '.[:5]'
echo

# 6. Ручное сканирование портов
echo "6. Запуск ручного сканирования:"
curl -X POST -s "$BASE_URL/scan" | python3 -m json.tool
echo

# 7. Получение устройств с портами
echo "7. Устройства с известными портами:"
curl -s "$BASE_URL/devices" | jq '.[] | select(.port != null) | {name: .gateway_name, port: .port, model: .device_model}'
echo

# 8. Статистика по моделям устройств
echo "8. Статистика по моделям:"
curl -s "$BASE_URL/devices" | jq 'group_by(.device_model) | map({model: .[0].device_model, count: length})'
echo

# 9. Устройства по статусу
echo "9. Группировка по статусу:"
curl -s "$BASE_URL/devices" | jq 'group_by(.port_scan_status) | map({status: .[0].port_scan_status, count: length})'
echo

echo "=== Примеры команд для перезагрузки ==="
echo "# Перезагрузка устройства Vochi-Main:"
echo "curl -X POST $BASE_URL/devices/Vochi-Main/reboot"
echo

echo "=== Мониторинг логов ==="
echo "# Просмотр логов в реальном времени:"
echo "tail -f /var/log/goip_service.log"
echo

echo "# Поиск ошибок в логах:"
echo "grep -i error /var/log/goip_service.log"
echo

echo "=== Управление сервисом ==="
echo "./goip.sh start    # Запуск сервиса"
echo "./goip.sh stop     # Остановка сервиса"
echo "./goip.sh restart  # Перезапуск сервиса"
echo "./goip.sh status   # Проверка статуса" 