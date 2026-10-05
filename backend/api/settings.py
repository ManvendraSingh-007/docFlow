import re
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from backend.auth import current_user
from backend.database import AccountSettingsRepository, UserRepository
from backend.security import hash_password, verify_password
from backend.services.webhook_dispatcher import WebhookDispatcher

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdatePayload(BaseModel):
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    default_provider: Optional[str] = None
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    auto_dispatch_on_verified: Optional[bool] = None

@router.get("")
def get_current_settings(user: dict = Depends(current_user)):
    account = AccountSettingsRepository.get(user["id"])
    return {
        "webhook_url": account.get("webhook_url", ""),
        "webhook_secret_configured": bool(account.get("webhook_secret")),
        "default_provider": account.get("default_provider", "auto"),
        "auto_dispatch_on_verified": bool(account.get("auto_dispatch_on_verified")),
        "gemini_api_key_configured": bool(account.get("gemini_api_key")),
        "openai_api_key_configured": bool(account.get("openai_api_key")),
        "inbound_token_configured": bool(account.get("inbound_token")),
        "full_name": user["full_name"],
        "email": user["email"],
        "company_name": user["company_name"],
    }

@router.post("")
def update_settings(payload: SettingsUpdatePayload, user: dict = Depends(current_user)):
    values = payload.model_dump(exclude_unset=True)
    if values.get("default_provider") not in (None, "auto", "gemini", "openai", "heuristic"):
        raise HTTPException(status_code=422, detail="Choose a supported extraction provider")
    webhook_url = values.get("webhook_url")
    if webhook_url and not re.fullmatch(r"https?://[^\s]+", webhook_url):
        raise HTTPException(status_code=422, detail="Webhook URL must use HTTP or HTTPS")
    AccountSettingsRepository.update(user["id"], values)
    return {"message": "Settings saved", "current": get_current_settings(user)}


class ProfileUpdatePayload(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    company_name: str = Field(default="", max_length=160)


class PasswordUpdatePayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.put("/profile")
def update_profile(payload: ProfileUpdatePayload, user: dict = Depends(current_user)):
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    existing = UserRepository.get_by_email(email)
    if existing and existing["id"] != user["id"]:
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    UserRepository.update_profile(user["id"], email, payload.full_name.strip(), payload.company_name.strip())
    return {"message": "Account details saved"}


@router.put("/password")
def update_password(payload: PasswordUpdatePayload, user: dict = Depends(current_user)):
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    UserRepository.update_password(user["id"], hash_password(payload.new_password))
    return {"message": "Password updated"}


@router.post("/inbound-token/rotate")
def rotate_inbound_token(user: dict = Depends(current_user)):
    token = AccountSettingsRepository.rotate_inbound_token(user["id"])
    return {"inbound_token": token}

@router.post("/test-webhook")
async def test_webhook_connection(url: Optional[str] = None, user: dict = Depends(current_user)):
    account = AccountSettingsRepository.get(user["id"])
    target = url or account.get("webhook_url", "")
    if not target:
        raise HTTPException(status_code=400, detail="Please provide or save a webhook URL first.")

    payload = {
        "event": "docflow.test_ping",
        "message": "DocFlow Webhook connection verified successfully!",
        "timestamp": 1728100000
    }
    success, code, body, duration = await WebhookDispatcher.dispatch(target, payload, account.get("webhook_secret", ""))
    return {
        "success": success,
        "status_code": code,
        "duration_ms": duration,
        "response": body
    }
