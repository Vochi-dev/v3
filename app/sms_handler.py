import requests
from datetime import datetime
from bs4 import BeautifulSoup
from .database import save_sms
from .config import settings

def parse_sms_time(time_str):
    return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')

def fetch_and_save_sms():
    try:
        # Получаем SMS с сервера
        response = requests.get(
            f"{settings.SMS_SERVER_URL}/goip/en/receive.php",
            auth=(settings.SMS_SERVER_USERNAME, settings.SMS_SERVER_PASSWORD)
        )
        response.raise_for_status()
        
        # Парсим HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table')
        
        if not table:
            return []
        
        rows = table.find_all('tr')[1:]  # Пропускаем заголовок
        sms_list = []
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 4:
                receive_time = parse_sms_time(cols[0].text.strip())
                source_number = cols[1].text.strip()
                receive_goip = cols[2].text.strip()
                sms_text = cols[3].text.strip()
                
                # Сохраняем в базу данных
                save_sms(receive_time, source_number, receive_goip, sms_text)
                
                sms_list.append({
                    'receive_time': receive_time,
                    'source_number': source_number,
                    'receive_goip': receive_goip,
                    'sms_text': sms_text
                })
        
        return sms_list
        
    except Exception as e:
        print(f"Error fetching SMS: {str(e)}")
        return [] 