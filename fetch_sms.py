from app.sms_handler import fetch_and_save_sms
from app.database import create_tables

def main():
    try:
        # Создаем таблицы если они не существуют
        create_tables()
        print("Database tables are ready")
        
        # Получаем и сохраняем SMS
        sms_list = fetch_and_save_sms()
        print(f"Retrieved and saved {len(sms_list)} SMS messages")
        
        # Выводим полученные SMS
        for sms in sms_list:
            print(f"\nTime: {sms['receive_time']}")
            print(f"From: {sms['source_number']}")
            print(f"GoIP: {sms['receive_goip']}")
            print(f"Text: {sms['sms_text']}")
            
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    main() 