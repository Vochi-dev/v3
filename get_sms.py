#!/usr/bin/env python3
import requests
import json

def get_sms():
    url = "http://91.149.128.210/goip/querysms/"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "auth": {
            "username": "root",
            "password": "gjitkdjy4070+37529AAA"
        },
        "taskID": "*"  # запрашиваем все сообщения
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        print(f"Status Code: {response.status_code}")
        print(f"Response Text: {response.text}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"JSON Response: {json.dumps(data, indent=2, ensure_ascii=False)}")
            except json.JSONDecodeError:
                print("Response is not JSON format")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_sms() 