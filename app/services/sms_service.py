import requests
import urllib3
from datetime import datetime
from typing import List, Dict, Optional
from app.config import settings

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class SMSService:
    def __init__(self):
        self.base_url = settings.SMS_SERVER_URL
        self.username = settings.SMS_SERVER_USERNAME
        self.password = settings.SMS_SERVER_PASSWORD
        self.session = requests.Session()
        
    def _login(self) -> bool:
        """
        Выполняет вход на SMS сервер.
        Возвращает True если успешно, False если нет.
        """
        try:
            login_data = {
                "username": self.username,
                "password": self.password,
                "Submit": "Sign in",
                "lan": "3"
            }
            
            response = self.session.post(
                f"{self.base_url}/goip/en/dologin.php",
                data=login_data,
                verify=False,
                timeout=5,
                allow_redirects=True
            )
            
            return response.status_code == 200 and "login" not in response.text.lower()
            
        except Exception as e:
            print(f"Ошибка при входе: {e}")
            return False
    
    def get_messages(self, message_type: str = "all", page: int = 1) -> List[Dict]:
        """
        Получает список SMS сообщений.
        message_type: "all", "inbox", "sent"
        page: номер страницы (30 сообщений на странице)
        """
        if not self._login():
            raise Exception("Не удалось войти на SMS сервер")
            
        try:
            # Определяем параметры в зависимости от типа сообщений
            params = {}
            if message_type == "inbox":
                params["type"] = "1"
            elif message_type == "sent":
                params["type"] = "2"
                
            if page > 1:
                params["page"] = str(page)
            
            # Пробуем разные URL для получения сообщений
            urls = [
                "/goip/en/receive.php",
                "/goip/en/receivebox.php"
            ]
            
            for url in urls:
                try:
                    response = self.session.get(
                        f"{self.base_url}{url}",
                        params=params,
                        verify=False,
                        timeout=5
                    )
                    
                    # Если получили форму логина, пробуем войти снова
                    if "login" in response.text.lower():
                        if not self._login():
                            continue
                        response = self.session.get(
                            f"{self.base_url}{url}",
                            params=params,
                            verify=False,
                            timeout=5
                        )
                    
                    # Если получили правильный ответ
                    if response.status_code == 200 and len(response.text) > 100:
                        # TODO: Добавить парсинг HTML и извлечение сообщений
                        # Пока возвращаем пустой список
                        return []
                        
                except requests.exceptions.Timeout:
                    continue
                except Exception as e:
                    print(f"Ошибка при запросе {url}: {e}")
                    continue
            
            raise Exception("Не удалось получить сообщения ни по одному URL")
            
        except Exception as e:
            print(f"Ошибка при получении сообщений: {e}")
            return []
    
    def send_message(self, phone: str, text: str) -> bool:
        """
        Отправляет SMS сообщение.
        Возвращает True если успешно, False если нет.
        """
        if not self._login():
            raise Exception("Не удалось войти на SMS сервер")
            
        try:
            data = {
                "action": "send",
                "phone": phone,
                "message": text
            }
            
            response = self.session.post(
                f"{self.base_url}/goip/en/dosend.php",
                data=data,
                verify=False,
                timeout=5
            )
            
            return response.status_code == 200 and "success" in response.text.lower()
            
        except Exception as e:
            print(f"Ошибка при отправке SMS: {e}")
            return False

# Создаем глобальный экземпляр сервиса
sms_service = SMSService() 