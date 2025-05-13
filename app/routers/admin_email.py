

from fastapi import APIRouter, Request, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import csv
from io import TextIOWrapper
from app.services.users import (
    get_all_emails,
    add_or_update_emails_from_file,
)

router = APIRouter()


@router.get("/admin/email-users", response_class=HTMLResponse)
async def email_users_admin(request: Request):
    users = get_all_emails()
    users.sort(key=lambda x: x["number"])
    rows = "".join([
        f"<tr><td>{u['number']}</td><td>{u['email']}</td><td>{u['name']}</td>"
        f"<td><input type='checkbox' {'checked' if u['right_all'] else ''}></td>"
        f"<td><input type='checkbox' {'checked' if u['right_1'] else ''}></td>"
        f"<td><input type='checkbox' {'checked' if u['right_2'] else ''}></td></tr>"
        for u in users
    ])
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


@router.post("/admin/upload-emails")
async def upload_emails(file: UploadFile):
    text_wrapper = TextIOWrapper(file.file, encoding='utf-8')
    reader = csv.DictReader(text_wrapper)
    new_entries = []
    for row in reader:
        email = row.get("Email") or row.get("email") or ""
        name = row.get("NAME") or row.get("Name") or row.get("name") or ""
        if email:
            new_entries.append({"email": email.strip(), "name": name.strip()})
    add_or_update_emails_from_file(new_entries)
    return RedirectResponse(url="/admin/email-users", status_code=303)
