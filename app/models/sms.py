from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON
from app.database import Base

class SMS(Base):
    __tablename__ = "sms"

    id = Column(Integer, primary_key=True)
    task_id = Column(String(50), index=True)  # ID задачи с SMS-сервера
    from_number = Column(String(20))  # Номер отправителя
    to_numbers = Column(JSON)  # Список номеров получателей
    content = Column(Text)  # Текст сообщения
    status = Column(String(20))  # Статус отправки
    goip_line = Column(String(20))  # Линия GoIP
    error_code = Column(String(10), nullable=True)  # Код ошибки, если есть
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<SMS(id={self.id}, task_id={self.task_id}, status={self.status})>" 