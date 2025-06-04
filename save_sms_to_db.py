import requests
from bs4 import BeautifulSoup
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_batch
import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Параметры подключения к БД из переменных окружения
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_NAME = os.getenv('DB_NAME', 'asterisk')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASS = os.getenv('DB_PASSWORD', '')

def get_sms_from_goip(page=1):
    """Получает SMS с GoIP сервера"""
    url = f"http://91.149.128.210/goip/en/receive.php?page={page}"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    sms_list = []
    # Находим все строки с SMS (исключая заголовок таблицы)
    rows = soup.find_all('tr', class_='even')
    
    for row in rows:
        cols = row.find_all('td')
        if len(cols) >= 7:  # Проверяем, что есть все нужные колонки
            # Парсим дату и время
            receive_time_str = cols[1].text.strip()
            receive_time = datetime.strptime(receive_time_str, '%Y-%m-%d %H:%M:%S')
            
            sms_data = {
                'receive_time': receive_time,
                'source_number': cols[3].text.strip(),  # Source Number
                'receive_goip': cols[5].text.strip(),   # Receive goip
                'sms_text': cols[6].text.strip()        # SMS Text
            }
            sms_list.append(sms_data)
    
    return sms_list

def save_sms_to_db(sms_list):
    """Сохраняет SMS в базу данных"""
    conn = psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
    
    try:
        with conn.cursor() as cur:
            # Подготавливаем данные для пакетной вставки
            values = [(
                sms['receive_time'],
                sms['source_number'],
                sms['receive_goip'],
                sms['sms_text']
            ) for sms in sms_list]
            
            # Выполняем пакетную вставку
            execute_batch(cur, """
                INSERT INTO incoming_sms (receive_time, source_number, receive_goip, sms_text)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, values)
            
        conn.commit()
        print(f"Successfully saved {len(sms_list)} SMS messages to database")
        
    except Exception as e:
        print(f"Error saving to database: {e}")
        conn.rollback()
    
    finally:
        conn.close()

def main():
    # Получаем первые 2 страницы (50 SMS)
    all_sms = []
    for page in range(1, 3):
        sms_list = get_sms_from_goip(page)
        all_sms.extend(sms_list)
        print(f"Retrieved {len(sms_list)} SMS from page {page}")
    
    # Сохраняем в базу данных
    save_sms_to_db(all_sms)

if __name__ == "__main__":
    main() 