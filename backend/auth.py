import secrets

from fastapi import HTTPException, Request

from backend.database import UserRepository


def csrf_token_for(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, submitted_token: str | None = None) -> None:
    expected = request.session.get("csrf_token")
    supplied = submitted_token or request.headers.get("x-csrf-token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Invalid request token")


def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = UserRepository.get_by_id(user_id)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Authentication required")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        validate_csrf(request)
    return user


def user_for_page(request: Request):
    user_id = request.session.get("user_id")
    return UserRepository.get_by_id(user_id) if user_id else None