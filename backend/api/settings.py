import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, HTTPException
from backend.config import settings, reload_settings, BASE_DIR
from backend.services.webhook_dispatcher import WebhookDispatcher

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdatePayload(BaseModel):
    default_webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    default_provider: Optional[str] = None # auto, gemini, openai, heuristic
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    auto_dispatch_on_verified: Optional[bool] = None

def mask_key(k: str) -> str:
    if not k or len(k) < 8:
        return "********" if k else ""
    return f"{k[:4]}...{k[-4:]}"

@router.get("")
def get_current_settings():
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "default_webhook_url": settings.default_webhook_url,
        "webhook_secret_set": bool(settings.webhook_secret),
        "default_provider": settings.default_provider,
        "auto_dispatch_on_verified": settings.auto_dispatch_on_verified,
        "gemini_api_key_configured": bool(settings.gemini_api_key),
        "gemini_api_key_preview": mask_key(settings.gemini_api_key),
        "openai_api_key_configured": bool(settings.openai_api_key),
        "openai_api_key_preview": mask_key(settings.openai_api_key),
    }

@router.post("")
def update_settings(payload: SettingsUpdatePayload):
    env_path = BASE_DIR / ".env"
    existing_lines = []
    if env_path.exists():
        with open(env_path, "r") as f:
            existing_lines = f.readlines()

    env_dict = {}
    for line in existing_lines:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env_dict[k.strip()] = v.strip()

    if payload.default_webhook_url is not None:
        env_dict["DEFAULT_WEBHOOK_URL"] = payload.default_webhook_url
    if payload.webhook_secret is not None:
        env_dict["WEBHOOK_SECRET"] = payload.webhook_secret
    if payload.default_provider is not None:
        env_dict["DEFAULT_PROVIDER"] = payload.default_provider
    if payload.gemini_api_key is not None and payload.gemini_api_key != "":
        env_dict["GEMINI_API_KEY"] = payload.gemini_api_key
    if payload.openai_api_key is not None and payload.openai_api_key != "":
        env_dict["OPENAI_API_KEY"] = payload.openai_api_key
    if payload.auto_dispatch_on_verified is not None:
        env_dict["AUTO_DISPATCH_ON_VERIFIED"] = "true" if payload.auto_dispatch_on_verified else "false"

    # Write back
    with open(env_path, "w") as f:
        for k, v in env_dict.items():
            f.write(f"{k}={v}\n")

    reload_settings()
    return {"message": "Settings updated successfully", "current": get_current_settings()}

@router.post("/test-webhook")
async def test_webhook_connection(url: Optional[str] = None):
    target = url or settings.default_webhook_url
    if not target:
        raise HTTPException(status_code=400, detail="Please provide or save a webhook URL first.")

    payload = {
        "event": "docflow.test_ping",
        "message": "DocFlow Webhook connection verified successfully!",
        "timestamp": 1728100000
    }
    success, code, body, duration = await WebhookDispatcher.dispatch(target, payload)
    return {
        "success": success,
        "status_code": code,
        "duration_ms": duration,
        "response": body
    }
