#!/usr/bin/env python3

from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

if __name__ == "__main__":
    print("🧪 Запускаю минимальный тест на порту 8025...")
    uvicorn.run(app, host="0.0.0.0", port=8025)
