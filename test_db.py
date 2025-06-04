import psycopg2
from app.database import DB_CONFIG, create_tables

def test_connection():
    try:
        # Пробуем подключиться к базе данных
        conn = psycopg2.connect(**DB_CONFIG)
        print("Successfully connected to PostgreSQL")
        
        # Создаем таблицы
        create_tables()
        print("Tables created successfully")
        
        # Закрываем соединение
        conn.close()
        print("Connection closed")
        
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    test_connection() 