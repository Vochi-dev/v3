from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import ADMIN_PASSWORD

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

# Функция для проверки авторизации
def require_login(request: Request):
    if request.cookies.get("session") != "valid":
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )

# Роут для отображения формы логина
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

# Роут для обработки отправки формы логина
@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    # Проверка пароля
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401
        )
    
    # Установка сессионной куки
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(key="session", value="valid", httponly=True, max_age=86400, path="/admin")
    return response

# Роут для главной страницы панели администратора
@router.get("/", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# Новый пункт меню для управления email-пользователями
@router.get("/email-users", dependencies=[Depends(require_login)], response_class=HTMLResponse)
async def email_users_admin(request: Request):
    # Получаем список email пользователей
    users = get_all_emails()  # Должен быть реализован в app/services/users.py
    users.sort(key=lambda x: x["number"])
    
    rows = "".join([
        f"<tr><td>{u['number']}</td><td>{u['email']}</td><td>{u['name']}</td>"
        f"<td><input type='checkbox' {'checked' if u['right_all'] else ''}></td>"
        f"<td><input type='checkbox' {'checked' if u['right_1'] else ''}></td>"
        f"<td><input type='checkbox' {'checked' if u['right_2'] else ''}></td></tr>"
        for u in users
    ])
    
    # HTML-шаблон для страницы с email пользователями
    return f"""
    <h1>Email-пользователи</h1>
    <form method="post" enctype="multipart/form-data" action="/admin/upload-emails">
      <input type="file" name="file">
      <button type="submit">Загрузить</button>
    </form>
    <table border="1">
      <tr><th>№</th><th>Email</th><th>Название</th><th>All</th><th>1</th><th>2</th></tr>
      {rows}
    </table>
    """

# Роут для загрузки файла с email пользователями
@router.post("/admin/upload-emails")
async def upload_emails(file: UploadFile):
    # Чтение и обработка файла
    text_wrapper = TextIOWrapper(file.file, encoding='utf-8')
    reader = csv.DictReader(text_wrapper)
    new_entries = []
    
    for row in reader:
        email = row.get("Email") or row.get("email") or ""
        name = row.get("NAME") or row.get("Name") or row.get("name") or ""
        
        if email:
            new_entries.append({"email": email.strip(), "name": name.strip()})
    
    # Добавление или обновление email пользователей в базу
    add_or_update_emails_from_file(new_entries)  # Должен быть реализован в app/services/users.py
    return RedirectResponse(url="/admin/email-users", status_code=303)
