import re
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, Request
from backend.auth import current_user
from backend.database import AccountSettingsRepository, ProviderCredentialRepository, UserRepository, get_connection
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


def _credentials_unlocked(request: Request):
    until = request.session.get("credential_unlock_until", 0)
    if not isinstance(until, (int, float)) or until < datetime.now(timezone.utc).timestamp():
        request.session.pop("credential_unlock_until", None)
        raise HTTPException(status_code=423, detail="Unlock API key settings with your PIN first")


class CredentialPinPayload(BaseModel):
    pin: str = Field(min_length=6, max_length=12, pattern=r"^\d{6,12}$")
    current_password: str = Field(min_length=1, max_length=256)


class CredentialUnlockPayload(BaseModel):
    pin: str = Field(min_length=6, max_length=12, pattern=r"^\d{6,12}$")


class CredentialPayload(BaseModel):
    provider: Literal["gemini", "openai", "mistral", "openrouter"]
    label: str = Field(min_length=1, max_length=80)
    api_key: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=160)


class CredentialUpdatePayload(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    api_key: Optional[str] = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=160)


@router.get("/credentials/status")
def credential_status(user: dict = Depends(current_user)):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM credential_security WHERE user_id = ?", (user["id"],)).fetchone()
    conn.close()
    return {"pin_configured": bool(row)}


@router.post("/credentials/pin")
def set_credentials_pin(payload: CredentialPinPayload, request: Request, user: dict = Depends(current_user)):
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    with conn:
        conn.execute("INSERT INTO credential_security (user_id, pin_hash, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET pin_hash = excluded.pin_hash, failed_attempts = 0, locked_until = NULL, updated_at = excluded.updated_at", (user["id"], hash_password(payload.pin), now))
    conn.close()
    request.session["credential_unlock_until"] = datetime.now(timezone.utc).timestamp() + 300
    return {"message": "PIN saved. API key settings are unlocked for five minutes."}


@router.post("/credentials/unlock")
def unlock_credentials(payload: CredentialUnlockPayload, request: Request, user: dict = Depends(current_user)):
    conn = get_connection()
    row = conn.execute("SELECT * FROM credential_security WHERE user_id = ?", (user["id"],)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=409, detail="Set an API key PIN first")
    now = datetime.now(timezone.utc)
    locked_until = row["locked_until"]
    if locked_until and datetime.fromisoformat(locked_until) > now:
        conn.close()
        raise HTTPException(status_code=429, detail="PIN locked temporarily. Try again later.")
    valid = verify_password(payload.pin, row["pin_hash"])
    with conn:
        if valid:
            conn.execute("UPDATE credential_security SET failed_attempts = 0, locked_until = NULL WHERE user_id = ?", (user["id"],))
        else:
            attempts = row["failed_attempts"] + 1
            locked = (now + timedelta(minutes=5)).isoformat() if attempts >= 5 else None
            conn.execute("UPDATE credential_security SET failed_attempts = ?, locked_until = ? WHERE user_id = ?", (0 if locked else attempts, locked, user["id"]))
    conn.close()
    if not valid:
        raise HTTPException(status_code=403, detail="PIN is incorrect")
    request.session["credential_unlock_until"] = now.timestamp() + 300
    return {"message": "API key settings unlocked for five minutes."}


@router.post("/credentials/lock")
def lock_credentials(request: Request, user: dict = Depends(current_user)):
    request.session.pop("credential_unlock_until", None)
    return {"message": "API key settings locked"}


@router.get("/credentials")
def list_credentials(request: Request, user: dict = Depends(current_user)):
    _credentials_unlocked(request)
    return {"credentials": ProviderCredentialRepository.list_for_user(user["id"])}


@router.post("/credentials")
def add_credential(payload: CredentialPayload, request: Request, user: dict = Depends(current_user)):
    _credentials_unlocked(request)
    label, key, model = payload.label.strip(), payload.api_key.strip(), payload.model.strip()
    if not label or not key or not model:
        raise HTTPException(status_code=422, detail="Name, API key, and model ID are required")
    credential_id = ProviderCredentialRepository.create(user["id"], payload.provider, label, key, model)
    return {"id": credential_id}


@router.put("/credentials/{credential_id}")
def edit_credential(credential_id: str, payload: CredentialUpdatePayload, request: Request, user: dict = Depends(current_user)):
    _credentials_unlocked(request)
    label, model = payload.label.strip(), payload.model.strip()
    api_key = payload.api_key.strip() if payload.api_key else None
    if not label or not model or (payload.api_key and not api_key):
        raise HTTPException(status_code=422, detail="Name and model ID are required; replacement key cannot be blank")
    if not ProviderCredentialRepository.update(user["id"], credential_id, label, model, api_key):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key updated"}


@router.post("/credentials/{credential_id}/select")
def select_credential(credential_id: str, request: Request, user: dict = Depends(current_user)):
    _credentials_unlocked(request)
    if not ProviderCredentialRepository.select(user["id"], credential_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key selected"}


@router.delete("/credentials/{credential_id}")
def delete_credential(credential_id: str, request: Request, user: dict = Depends(current_user)):
    _credentials_unlocked(request)
    if not ProviderCredentialRepository.delete(user["id"], credential_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key removed"}

@router.post("")
def update_settings(payload: SettingsUpdatePayload, user: dict = Depends(current_user)):
    values = payload.model_dump(exclude_unset=True)
    if values.get("default_provider") not in (None, "auto", "gemini", "openai", "mistral", "openrouter", "heuristic"):
        raise HTTPException(status_code=422, detail="Choose a supported extraction provider")
    if values.get("openrouter_model") is not None and (not values["openrouter_model"].strip() or len(values["openrouter_model"]) > 160):
        raise HTTPException(status_code=422, detail="Enter a valid OpenRouter model ID")
    for model_field in ("gemini_model", "openai_model", "mistral_model"):
        if values.get(model_field) is not None and (not values[model_field].strip() or len(values[model_field]) > 160):
            raise HTTPException(status_code=422, detail="Model IDs must contain between 1 and 160 characters")
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
