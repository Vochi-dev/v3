#!/usr/bin/env python3
from app.services.sms_service import sms_service

def test_sms_service():
    print("Тестирование SMS сервиса...")
    print("-" * 50)
    
    # Тест получения входящих сообщений
    print("\nПолучаем входящие сообщения (страница 1)...")
    messages = sms_service.get_messages(message_type="inbox", page=1)
    print(f"Получено сообщений: {len(messages)}")
    for msg in messages:
        print(f"Сообщение: {msg}")
    
    # Тест получения исходящих сообщений
    print("\nПолучаем исходящие сообщения (страница 1)...")
    messages = sms_service.get_messages(message_type="sent", page=1)
    print(f"Получено сообщений: {len(messages)}")
    for msg in messages:
        print(f"Сообщение: {msg}")
    
    # Тест отправки сообщения
    phone = "+375291234567"  # Замените на реальный номер для тестирования
    text = "Тестовое сообщение"
    
    print(f"\nПробуем отправить сообщение на номер {phone}...")
    result = sms_service.send_message(phone, text)
    if result:
        print("Сообщение успешно отправлено!")
    else:
        print("Ошибка при отправке сообщения")

if __name__ == "__main__":
    test_sms_service() 