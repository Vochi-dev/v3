import csv
from typing import List, Dict
from app.models import EmailUser  # Ваши модели данных, используемые в базе данных

# Функция для получения всех email пользователей из базы данных
def get_all_emails() -> List[Dict]:
    # Допустим, мы получаем всех пользователей из базы данных
    users = EmailUser.objects.all()  # Пример для ORM
    return [
        {"number": user.number, "email": user.email, "name": user.name, 
         "right_all": user.right_all, "right_1": user.right_1, "right_2": user.right_2}
        for user in users
    ]

# Функция для добавления или обновления email пользователей из файла
def add_or_update_emails_from_file(new_entries: List[Dict]):
    for entry in new_entries:
        # Пытаемся найти пользователя по email
        user = EmailUser.objects.filter(email=entry['email']).first()
        if user:
            # Обновляем существующего пользователя
            user.name = entry['name']
            user.save()
        else:
            # Создаем нового пользователя
            EmailUser.objects.create(email=entry['email'], name=entry['name'])
