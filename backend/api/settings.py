import re
from pydantic import BaseModel, Field
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Depends
from backend.auth import current_user
from backend.database import AccountSettingsRepository, ProviderCredentialRepository, UserRepository
from backend.security import hash_password, verify_password
from backend.services.webhook_dispatcher import WebhookDispatcher

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdatePayload(BaseModel):
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    default_provider: Optional[str] = None
    openrouter_model: Optional[str] = None
    gemini_model: Optional[str] = None
    openai_model: Optional[str] = None
    mistral_model: Optional[str] = None
    auto_dispatch_on_verified: Optional[bool] = None

@router.get("")
def get_current_settings(user: dict = Depends(current_user)):
    account = AccountSettingsRepository.get(user["id"])
    credentials = account.get("provider_credentials", {})
    return {
        "webhook_url": account.get("webhook_url", ""),
        "webhook_secret_configured": bool(account.get("webhook_secret")),
        "default_provider": account.get("default_provider", "auto"),
        "auto_dispatch_on_verified": bool(account.get("auto_dispatch_on_verified")),
        "gemini_api_key_configured": bool(credentials.get("gemini", {}).get("api_key")),
        "openai_api_key_configured": bool(credentials.get("openai", {}).get("api_key")),
        "mistral_api_key_configured": bool(credentials.get("mistral", {}).get("api_key")),
        "openrouter_api_key_configured": bool(credentials.get("openrouter", {}).get("api_key")),
        "openrouter_model": account.get("openrouter_model", "google/gemini-2.5-flash"),
        "gemini_model": account.get("gemini_model", "gemini-3.1-flash-lite"),
        "openai_model": account.get("openai_model", "gpt-4o-mini"),
        "mistral_model": account.get("mistral_model", "mistral-small-2506"),
        "inbound_token_configured": bool(account.get("inbound_token")),
        "full_name": user["full_name"],
        "email": user["email"],
        "company_name": user["company_name"],
    }


class CredentialPayload(BaseModel):
    provider: Literal["gemini", "openai", "mistral", "openrouter"]
    label: str = Field(min_length=1, max_length=80)
    api_key: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=160)


class CredentialUpdatePayload(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    api_key: Optional[str] = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=160)
@router.get("/credentials")
def list_credentials(user: dict = Depends(current_user)):
    return {"credentials": ProviderCredentialRepository.list_for_user(user["id"])}


@router.post("/credentials")
def add_credential(payload: CredentialPayload, user: dict = Depends(current_user)):
    label, key, model = payload.label.strip(), payload.api_key.strip(), payload.model.strip()
    if not label or not key or not model:
        raise HTTPException(status_code=422, detail="Name, API key, and model ID are required")
    credential_id = ProviderCredentialRepository.create(user["id"], payload.provider, label, key, model)
    return {"id": credential_id}


@router.put("/credentials/{credential_id}")
def edit_credential(credential_id: str, payload: CredentialUpdatePayload, user: dict = Depends(current_user)):
    label, model = payload.label.strip(), payload.model.strip()
    api_key = payload.api_key.strip() if payload.api_key else None
    if not label or not model or (payload.api_key and not api_key):
        raise HTTPException(status_code=422, detail="Name and model ID are required; replacement key cannot be blank")
    if not ProviderCredentialRepository.update(user["id"], credential_id, label, model, api_key):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key updated"}


@router.post("/credentials/{credential_id}/select")
def select_credential(credential_id: str, user: dict = Depends(current_user)):
    if not ProviderCredentialRepository.select(user["id"], credential_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key selected"}


@router.post("/credentials/{credential_id}/delete")
def delete_credential(credential_id: str, user: dict = Depends(current_user)):
    if not ProviderCredentialRepository.delete(user["id"], credential_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key removed"}

@router.post("")
def update_settings(payload: SettingsUpdatePayload, user: dict = Depends(current_user)):
    values = payload.model_dump(exclude_unset=True)
    if values.get("default_provider") not in (None, "auto", "gemini", "openai", "mistral", "openrouter"):
        raise HTTPException(status_code=422, detail="Choose a supported extraction provider")
    if values.get("openrouter_model") is not None and (not values["openrouter_model"].strip() or len(values["openrouter_model"]) > 160):
        raise HTTPException(status_code=422, detail="Enter a valid OpenRouter model ID")
    for model_field in ("gemini_model", "openai_model", "mistral_model"):
        if values.get(model_field) is not None and (not values[model_field].strip() or len(values[model_field]) > 160):
            raise HTTPException(status_code=422, detail="Model IDs must contain between 1 and 160 characters")
    webhook_url = values.get("webhook_url")
    if webhook_url and not re.fullmatch(r"https?://[^\s]+", webhook_url):
        raise HTTPException(status_code=422, detail="Webhook URL must use HTTP or HTTPS")
    if values.get("default_provider") and values["default_provider"] != "auto":
        ProviderCredentialRepository.activate_provider(user["id"], values["default_provider"])
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
