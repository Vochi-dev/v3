from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import asyncpg
from app.services.database import connect_to_db, close_db_connection
from app.routers import auth, dashboard, enterprise, fail2ban, monitoring, settings, mobile

app = FastAPI()

# Подключение роутеров
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(enterprise.router)
app.include_router(fail2ban.router)
app.include_router(monitoring.router)
app.include_router(settings.router)
app.include_router(mobile.router)

# Подключение статических файлов
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
async def startup():
    await connect_to_db()

@app.on_event("shutdown")
async def shutdown():
    await close_db_connection()

@app.middleware("http")
async def add_auth_redirect(request: Request, call_next):
    # Если это не страница логина и пользователь не аутентифицирован, перенаправляем на логин
    if not request.url.path.startswith(('/auth', '/static', '/admin')) and 'user' not in request.session:
        return RedirectResponse(url='/auth/login')
    response = await call_next(request)
    return response

@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard") 