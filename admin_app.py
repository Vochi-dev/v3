from fastapi import FastAPI
from app.routers.admin import router as admin_router
from app.routers.enterprise import router as enterprise_router
from app.routers.user_requests import router as requests_router

app = FastAPI()

# Основной раздел админки
app.include_router(admin_router)

# Раздел CRUD для предприятий
app.include_router(enterprise_router)

# Раздел управления заявками на подключение
app.include_router(requests_router)
