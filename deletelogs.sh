#!/bin/bash

# Скрипт для очистки логов Call Logger для конкретного предприятия
# Использование: ./deletelogs.sh 0367

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
    exit 1
fi

ENTERPRISE=$1

# Проверка формата номера предприятия (должно быть 4 цифры)
if ! [[ $ENTERPRISE =~ ^[0-9]{4}$ ]]; then
    echo -e "${RED}❌ Ошибка: неверный формат номера предприятия${NC}"
    echo "Номер должен состоять из 4 цифр (например: 0367)"
    exit 1
fi

# Чтение конфигурации БД
if [ ! -f "db_readme.txt" ]; then
    echo -e "${RED}❌ Ошибка: файл db_readme.txt не найден${NC}"
    exit 1
fi

# Извлекаем параметры подключения из db_readme.txt
DB_HOST="localhost"  # По умолчанию localhost
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

echo -e "${BLUE}🗑️  Очистка логов Call Logger${NC}"
echo -e "${BLUE}═══════════════════════════════${NC}"
echo ""
echo -e "Предприятие: ${YELLOW}$ENTERPRISE${NC}"
echo ""

# Проверяем существование партиции
echo -e "${BLUE}📋 Проверка партиции...${NC}"
PARTITION_EXISTS=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT COUNT(*) 
    FROM pg_tables 
    WHERE schemaname = 'public' 
    AND tablename = '$ENTERPRISE';
" | xargs)

if [ "$PARTITION_EXISTS" -eq 0 ]; then
    echo -e "${RED}❌ Партиция \"$ENTERPRISE\" не существует${NC}"
    echo ""
    echo "Доступные партиции:"
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT tablename as partition
        FROM pg_tables 
        WHERE schemaname = 'public' 
        AND tablename ~ '^[0-9]{4}$'
        ORDER BY tablename;
    "
    exit 1
fi

# Получаем статистику перед удалением
echo -e "${BLUE}📊 Статистика перед удалением:${NC}"
RECORDS_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT COUNT(*) FROM \"$ENTERPRISE\";
" | xargs)

if [ "$RECORDS_COUNT" -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Партиция \"$ENTERPRISE\" уже пуста${NC}"
    exit 0
fi

echo -e "  Записей в партиции: ${YELLOW}$RECORDS_COUNT${NC}"
echo ""
echo -e "${BLUE}🗑️  Удаление записей...${NC}"

# Выполняем очистку
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
    DELETE FROM \"$ENTERPRISE\";
" > /dev/null

# Проверяем результат
RECORDS_AFTER=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT COUNT(*) FROM \"$ENTERPRISE\";
" | xargs)

if [ "$RECORDS_AFTER" -eq 0 ]; then
    echo -e "${GREEN}✅ Успешно удалено записей: $RECORDS_COUNT${NC}"
    echo -e "${GREEN}✅ Партиция \"$ENTERPRISE\" очищена${NC}"
else
    echo -e "${RED}❌ Ошибка: не все записи были удалены${NC}"
    echo -e "Осталось записей: $RECORDS_AFTER"
    exit 1
fi

# Показываем финальную статистику
echo ""
echo -e "${BLUE}📊 Финальная статистика:${NC}"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
    SELECT 
        '$ENTERPRISE' as partition,
        COUNT(*) as records,
        pg_size_pretty(pg_total_relation_size('\"$ENTERPRISE\"')) as size
    FROM \"$ENTERPRISE\";
"

echo ""
echo -e "${GREEN}✅ Готово!${NC}"

# Очищаем переменную окружения с паролем
unset PGPASSWORD

