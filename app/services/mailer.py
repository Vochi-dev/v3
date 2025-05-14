import smtplib
from email.message import EmailMessage
from app.config import EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, EMAIL_FROM

def send_email(to: str, subject: str, text: str):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"]   = to
    msg["Subject"] = subject
    msg.set_content(text)

    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as s:
        if EMAIL_USE_TLS:
            s.starttls()
        s.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
        s.send_message(msg)
