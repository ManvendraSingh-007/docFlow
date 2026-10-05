import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.auth import csrf_token_for, validate_csrf
from backend.config import BASE_DIR
from backend.database import UserRepository
from backend.security import hash_password, verify_password

router = APIRouter(tags=["authentication"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
DUMMY_PASSWORD_HASH = hash_password("docflow-invalid-account-password-check")


def _auth_page(request: Request, mode: str, error: str = "", status_code: int = 200):
    return templates.TemplateResponse(
        request,
        "auth.html",
        {"mode": mode, "error": error, "csrf_token": csrf_token_for(request)},
        status_code=status_code,
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return _auth_page(request, "register")


@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(...),
    company_name: str = Form(""),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    email = email.strip().lower()
    full_name = full_name.strip()
    company_name = company_name.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return _auth_page(request, "register", "Enter a valid email address.", 400)
    if not full_name or len(full_name) > 120 or len(company_name) > 160:
        return _auth_page(request, "register", "Check the name and company fields.", 400)
    if len(password) < 12 or len(password) > 256:
        return _auth_page(request, "register", "Use a password between 12 and 256 characters.", 400)
    if UserRepository.get_by_email(email):
        return _auth_page(request, "register", "An account with that email already exists.", 409)
    try:
        user = UserRepository.create(email, full_name, company_name, hash_password(password))
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            return _auth_page(request, "register", "An account with that email already exists.", 409)
        raise
    request.session.clear()
    request.session["user_id"] = user["id"]
    csrf_token_for(request)
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return _auth_page(request, "login")


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    email = email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    if UserRepository.failed_login_count(client_ip, since) >= 10:
        return _auth_page(request, "login", "Too many attempts. Try again in 15 minutes.", 429)
    user = UserRepository.get_by_email(email)
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    password_valid = verify_password(password, password_hash)
    UserRepository.record_login_attempt(email, client_ip, bool(user and password_valid), since)
    if not user or not password_valid:
        return _auth_page(request, "login", "Email or password is incorrect.", 401)
    request.session.clear()
    request.session["user_id"] = user["id"]
    csrf_token_for(request)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)