from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.services.enterprise import (
    list_enterprises,
    get_enterprise,
    create_enterprise,
    update_enterprise,
    delete_enterprise,
)

env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html", "xml"])
)

router = APIRouter(prefix="/admin/enterprises", tags=["enterprises"])

def render(template_name: str, **context):
    tmpl = env.get_template(template_name)
    return HTMLResponse(tmpl.render(**context))

@router.get("/")
async def enterprises_list(request: Request):
    items = list_enterprises()
    return render("enterprise_list.html", request=request, enterprises=items)

@router.get("/create")
async def enterprise_create_form(request: Request):
    return render("enterprise_form.html", request=request, enterprise=None)

@router.post("/create")
async def enterprise_create(
    number: str = Form(...),
    name: str = Form(...),
    name2: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
):
    if get_enterprise(number):
        raise HTTPException(status_code=400, detail="Enterprise already exists")
    create_enterprise(number, name, name2, bot_token, chat_id, ip, secret, host)
    return RedirectResponse(url="/admin/enterprises", status_code=303)

@router.get("/edit/{number}")
async def enterprise_edit_form(request: Request, number: str):
    ent = get_enterprise(number)
    if not ent:
        raise HTTPException(status_code=404, detail="Not found")
    return render("enterprise_form.html", request=request, enterprise=ent)

@router.post("/edit/{number}")
async def enterprise_edit(
    number: str,
    name: str = Form(...),
    name2: str = Form(...),
    bot_token: str = Form(...),
    chat_id: str = Form(...),
    ip: str = Form(...),
    secret: str = Form(...),
    host: str = Form(...),
):
    if not get_enterprise(number):
        raise HTTPException(status_code=404, detail="Not found")
    update_enterprise(number, name, name2, bot_token, chat_id, ip, secret, host)
    return RedirectResponse(url="/admin/enterprises", status_code=303)

@router.post("/delete/{number}")
async def enterprise_delete(number: str):
    if not get_enterprise(number):
        raise HTTPException(status_code=404, detail="Not found")
    delete_enterprise(number)
    return RedirectResponse(url="/admin/enterprises", status_code=303)
