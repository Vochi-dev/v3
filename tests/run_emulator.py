#!/usr/bin/env python3
"""
Запуск сервера эмулятора событий звонков
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from tests.call_emulator.api_server import app

if __name__ == "__main__":
    print("🚀 Starting Call Event Emulator API Server")
    print("📡 URL: http://localhost:8030")
    print("📋 Docs: http://localhost:8030/docs")
    print("🔍 Health: http://localhost:8030/health")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8030,
        log_level="info"
    )