#!/bin/bash

# Скрипт для ПОЛНОЙ очистки логов Call Logger для конкретного предприятия
# Включает очистку файлов в call_tracer/ И ВСЕХ логов на хосте Asterisk
#
# Использование: ./deletelogs.sh 0367
#
# Что делает скрипт:
# 1. Очищает файлы логов в call_tracer/{enterprise}/ 
# 2. Подключается к хосту Asterisk по SSH
# 3. Удаляет SQLite файл Listen_AMI_*.db (основной лог AMI)
# 4. Очищает Master.csv (CDR записи)
# 5. Очищает verbose (лог Asterisk)
# 6. Очищает event.log (лог событий)
# 7. Перезапускает listen_AMI_python.service
# 8. Проверяет создание нового файла

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Параметры SSH для хостов Asterisk
SSH_PORT=5059
SSH_USER="root"
SSH_PASS="5atx9Ate@pbx"
ASTERISK_LOG_DIR="/var/log/asterisk"
ASTERISK_SERVICE="listen_AMI_python.service"

# Директория call_tracer
CALL_TRACER_DIR="/root/asterisk-webhook/call_tracer"

# Проверка аргументов
if [ $# -eq 0 ]; then
    echo -e "${RED}❌ Ошибка: не указан номер предприятия${NC}"
    echo ""
    echo "Использование: ./deletelogs.sh <enterprise_number>"
    echo ""
    echo "Примеры:"
    echo "  ./deletelogs.sh 0367    # Очистить логи предприятия 0367"
    echo "  ./deletelogs.sh 0280    # Очистить логи предприятия 0280"
    echo ""
    echo "Скрипт выполняет ПОЛНУЮ очистку:"
    echo "  1. call_tracer/{enterprise}/events.log* (файловые логи)"
    echo "  2. Хост Asterisk:"
    echo "     - Listen_AMI_*.db (SQLite лог AMI событий)"
    echo "     - Master.csv (CDR записи)"
    echo "     - verbose (лог Asterisk)"
    echo "     - event.log (лог событий)"
    echo "  3. Перезапуск listen_AMI_python.service"
    echo ""
    exit 1
fi

ENTERPRISE=$1

# Проверка формата номера предприятия (должно быть 4 цифры)
if ! [[ $ENTERPRISE =~ ^[0-9]{4}$ ]]; then
    echo -e "${RED}❌ Ошибка: неверный формат номера предприятия${NC}"
    echo "Номер должен состоять из 4 цифр (например: 0367)"
    exit 1
fi

# Чтение конфигурации БД (для получения IP хоста)
if [ ! -f "db_readme.txt" ]; then
    echo -e "${RED}❌ Ошибка: файл db_readme.txt не найден${NC}"
    exit 1
fi

# Извлекаем параметры подключения из db_readme.txt
DB_HOST="localhost"
DB_PORT=$(grep "^Port:" db_readme.txt | awk '{print $2}')
DB_NAME=$(grep "^Database:" db_readme.txt | awk '{print $2}')
DB_USER=$(grep "^Username:" db_readme.txt | awk '{print $2}')
DB_PASS=$(grep "^Password:" db_readme.txt | awk '{print $2}')

# Проверка что все параметры получены
if [ -z "$DB_HOST" ] || [ -z "$DB_PORT" ] || [ -z "$DB_NAME" ] || [ -z "$DB_USER" ] || [ -z "$DB_PASS" ]; then
    echo -e "${RED}❌ Ошибка: не удалось прочитать параметры подключения из db_readme.txt${NC}"
    exit 1
fi

export PGPASSWORD="$DB_PASS"

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   🗑️  ПОЛНАЯ ОЧИСТКА ЛОГОВ ПРЕДПРИЯТИЯ $ENTERPRISE${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════
# ЭТАП 1: Получение IP хоста из таблицы enterprises
# ═══════════════════════════════════════════════════════════════════

echo -e "${CYAN}📡 Этап 1: Получение IP хоста из БД...${NC}"

HOST_IP=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT ip FROM enterprises WHERE number = '$ENTERPRISE';
" | xargs)

if [ -z "$HOST_IP" ]; then
    echo -e "${RED}❌ Ошибка: предприятие $ENTERPRISE не найдено в таблице enterprises${NC}"
    echo ""
    echo "Доступные предприятия:"
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT number, ip, name FROM enterprises WHERE active = true ORDER BY number LIMIT 20;
    "
    exit 1
fi

echo -e "  Предприятие: ${YELLOW}$ENTERPRISE${NC}"
echo -e "  IP хоста:    ${YELLOW}$HOST_IP${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════
# ЭТАП 2: Очистка файлов логов в call_tracer/
# ═══════════════════════════════════════════════════════════════════

echo -e "${CYAN}📁 Этап 2: Очистка файлов логов в call_tracer/$ENTERPRISE/...${NC}"

TRACER_PATH="$CALL_TRACER_DIR/$ENTERPRISE"

if [ -d "$TRACER_PATH" ]; then
    # Подсчитываем файлы и их размер (оба формата: events.log* и events_*.log)
    FILE_COUNT=$(find "$TRACER_PATH" \( -name "events.log*" -o -name "events_*.log" \) 2>/dev/null | wc -l)
    TOTAL_SIZE=$(du -sh "$TRACER_PATH" 2>/dev/null | awk '{print $1}')
    
    if [ "$FILE_COUNT" -gt 0 ]; then
        echo -e "  Найдено файлов: ${YELLOW}$FILE_COUNT${NC}"
        echo -e "  Общий размер:   ${YELLOW}$TOTAL_SIZE${NC}"
        
        # Удаляем все файлы логов (оба формата)
        rm -f "$TRACER_PATH"/events.log*
        rm -f "$TRACER_PATH"/events_*.log
        
        echo -e "${GREEN}  ✅ Все файлы логов удалены${NC}"
    else
        echo -e "${YELLOW}  ⚠️  Файлы логов не найдены${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠️  Директория $TRACER_PATH не существует${NC}"
    echo -e "  (Это нормально, если предприятие ещё не делало звонков)"
fi
echo ""

# ═══════════════════════════════════════════════════════════════════
# ЭТАП 3: Очистка логов на хосте Asterisk
# ═══════════════════════════════════════════════════════════════════

echo -e "${CYAN}🖥️  Этап 3: Очистка логов на хосте Asterisk ($HOST_IP)...${NC}"

# Проверяем доступность sshpass
if ! command -v sshpass &> /dev/null; then
    echo -e "${RED}❌ Ошибка: sshpass не установлен${NC}"
    echo "Установите: apt-get install sshpass"
    echo ""
    echo -e "${YELLOW}⚠️  Ручная очистка на хосте:${NC}"
    echo "  ssh -p $SSH_PORT $SSH_USER@$HOST_IP"
    echo "  rm -f $ASTERISK_LOG_DIR/Listen_AMI_*.db"
    echo "  rm -f $ASTERISK_LOG_DIR/cdr-csv/Master.csv"
    echo "  truncate -s 0 $ASTERISK_LOG_DIR/verbose"
    echo "  truncate -s 0 $ASTERISK_LOG_DIR/event.log"
    echo "  systemctl restart $ASTERISK_SERVICE"
    exit 1
fi

# Функция для выполнения SSH команд
ssh_exec() {
    sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p "$SSH_PORT" "$SSH_USER@$HOST_IP" "$@"
}

# Проверяем доступность хоста
echo -e "  Проверка доступности хоста..."
if ! ssh_exec "echo 'OK'" > /dev/null 2>&1; then
    echo -e "${RED}❌ Ошибка: не удалось подключиться к хосту $HOST_IP:$SSH_PORT${NC}"
    echo ""
    echo -e "${YELLOW}⚠️  Возможные причины:${NC}"
    echo "  - Хост недоступен"
    echo "  - Неверный порт SSH"
    echo "  - Неверные учётные данные"
    exit 1
fi
echo -e "${GREEN}  ✅ Хост доступен${NC}"
echo ""

# --- 3.1: Удаляем SQLite файл Listen_AMI_*.db ---
echo -e "  ${YELLOW}3.1${NC} Удаление SQLite файла Listen_AMI_*.db..."
SQLITE_FILES=$(ssh_exec "ls $ASTERISK_LOG_DIR/Listen_AMI_*.db 2>/dev/null | wc -l" 2>/dev/null)
if [ "$SQLITE_FILES" -gt 0 ]; then
    ssh_exec "rm -f $ASTERISK_LOG_DIR/Listen_AMI_*.db" 2>/dev/null
    echo -e "${GREEN}       ✅ Удалено SQLite файлов: $SQLITE_FILES${NC}"
else
    echo -e "${YELLOW}       ⚠️  SQLite файлы не найдены${NC}"
fi

# --- 3.2: Очищаем Master.csv (CDR записи) ---
echo -e "  ${YELLOW}3.2${NC} Очистка Master.csv (CDR записи)..."
MASTER_SIZE=$(ssh_exec "stat -c%s $ASTERISK_LOG_DIR/cdr-csv/Master.csv 2>/dev/null || echo '0'" 2>/dev/null)
if [ "$MASTER_SIZE" -gt 0 ]; then
    ssh_exec "truncate -s 0 $ASTERISK_LOG_DIR/cdr-csv/Master.csv" 2>/dev/null
    echo -e "${GREEN}       ✅ Master.csv очищен (было $MASTER_SIZE байт)${NC}"
else
    echo -e "${YELLOW}       ⚠️  Master.csv уже пуст${NC}"
fi

# --- 3.3: Очищаем verbose (лог Asterisk) ---
echo -e "  ${YELLOW}3.3${NC} Очистка verbose (лог Asterisk)..."
VERBOSE_SIZE=$(ssh_exec "stat -c%s $ASTERISK_LOG_DIR/verbose 2>/dev/null || echo '0'" 2>/dev/null)
if [ "$VERBOSE_SIZE" -gt 0 ]; then
    ssh_exec "truncate -s 0 $ASTERISK_LOG_DIR/verbose" 2>/dev/null
    echo -e "${GREEN}       ✅ verbose очищен (было $VERBOSE_SIZE байт)${NC}"
else
    echo -e "${YELLOW}       ⚠️  verbose уже пуст${NC}"
fi

# --- 3.4: Очищаем event.log ---
echo -e "  ${YELLOW}3.4${NC} Очистка event.log..."
EVENT_SIZE=$(ssh_exec "stat -c%s $ASTERISK_LOG_DIR/event.log 2>/dev/null || echo '0'" 2>/dev/null)
if [ "$EVENT_SIZE" -gt 0 ]; then
    ssh_exec "truncate -s 0 $ASTERISK_LOG_DIR/event.log" 2>/dev/null
    echo -e "${GREEN}       ✅ event.log очищен (было $EVENT_SIZE байт)${NC}"
else
    echo -e "${YELLOW}       ⚠️  event.log уже пуст${NC}"
fi

echo ""

# ═══════════════════════════════════════════════════════════════════
# ЭТАП 4: Перезапуск сервиса listen_AMI_python
# ═══════════════════════════════════════════════════════════════════

echo -e "${CYAN}🔄 Этап 4: Перезапуск сервиса listen_AMI_python...${NC}"

ssh_exec "systemctl restart $ASTERISK_SERVICE" 2>/dev/null
sleep 2

# Проверяем статус сервиса
SERVICE_STATUS=$(ssh_exec "systemctl is-active $ASTERISK_SERVICE" 2>/dev/null)
if [ "$SERVICE_STATUS" = "active" ]; then
    echo -e "${GREEN}  ✅ Сервис $ASTERISK_SERVICE запущен${NC}"
else
    echo -e "${RED}  ❌ Сервис $ASTERISK_SERVICE не запустился (статус: $SERVICE_STATUS)${NC}"
fi

# Проверяем создание нового SQLite файла
echo -e "  Проверка создания нового SQLite файла..."
sleep 1
NEW_DB_FILE=$(ssh_exec "ls $ASTERISK_LOG_DIR/Listen_AMI_*.db 2>/dev/null | head -1" 2>/dev/null)

if [ -n "$NEW_DB_FILE" ]; then
    NEW_DB_SIZE=$(ssh_exec "stat -c%s $NEW_DB_FILE 2>/dev/null || echo '0'" 2>/dev/null)
    echo -e "${GREEN}  ✅ Создан новый файл: $(basename $NEW_DB_FILE) (${NEW_DB_SIZE} байт)${NC}"
else
    echo -e "${YELLOW}  ⚠️  SQLite файл ещё не создан (появится при первом событии)${NC}"
fi

echo ""

# ═══════════════════════════════════════════════════════════════════
# ФИНАЛЬНЫЙ ОТЧЁТ
# ═══════════════════════════════════════════════════════════════════

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ ПОЛНАЯ ОЧИСТКА ЗАВЕРШЕНА УСПЕШНО${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "📊 Итоги очистки:"
echo -e "  • call_tracer/$ENTERPRISE/: ${GREEN}очищен${NC}"
echo -e "  • Хост $HOST_IP:"
echo -e "    - Listen_AMI_*.db (SQLite): ${GREEN}удалён${NC}"
echo -e "    - Master.csv (CDR): ${GREEN}очищен${NC}"
echo -e "    - verbose (лог): ${GREEN}очищен${NC}"
echo -e "    - event.log: ${GREEN}очищен${NC}"
echo -e "  • Сервис listen_AMI_python: ${GREEN}перезапущен${NC}"
echo ""
echo -e "🧪 Теперь можно выполнять тестовый звонок!"
echo ""

# Очищаем переменную окружения с паролем
unset PGPASSWORD
