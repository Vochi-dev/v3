import psycopg2

# Параметры подключения
DB_CONFIG = {
    'dbname': 'asterisk_webhook',
    'user': 'postgres',
    'password': 'r/Yskqh/ZbZuvjb2b3ahfg==',
    'host': 'localhost',
    'port': '5432'
}

def clear_enterprises():
    try:
        # Подключаемся к базе данных
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Выполняем очистку таблицы
        cur.execute("DELETE FROM enterprises;")
        conn.commit()
        print("Таблица enterprises успешно очищена")
        
    except Exception as e:
        print(f"Ошибка при очистке таблицы: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    clear_enterprises() 