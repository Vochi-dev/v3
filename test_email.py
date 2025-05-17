import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

msg = EmailMessage()
msg["Subject"] = "Проверка почты"
msg["From"] = os.getenv("EMAIL_FROM")
msg["To"] = "evgeny.baevski@gmail.com"
msg.set_content("Это тестовое письмо от бота")

with smtplib.SMTP(os.getenv("EMAIL_HOST"), int(os.getenv("EMAIL_PORT"))) as server:
    server.starttls()
    server.login(os.getenv("EMAIL_HOST_USER"), os.getenv("EMAIL_HOST_PASSWORD"))
    server.send_message(msg)

print("Письмо отправлено успешно")
