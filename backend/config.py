import os
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "docflow.db"

# Ensure runtime directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Load .env if present
load_dotenv(BASE_DIR / ".env")

class Settings(BaseModel):
    app_name: str = "DocFlow"
    app_version: str = "1.0.0"
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR
    uploads_dir: Path = UPLOADS_DIR
    db_path: Path = DB_PATH
    
    # AI Engine Keys
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    default_provider: str = os.getenv("DEFAULT_PROVIDER", "auto") # auto, gemini, openai, heuristic
    
    # Outbound Webhook config
    default_webhook_url: str = os.getenv("DEFAULT_WEBHOOK_URL", "")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "docflow_sec_default_change_me")
    
    # Auto-dispatch flag: if True, send webhook immediately on successful VERIFIED parse
    auto_dispatch_on_verified: bool = os.getenv("AUTO_DISPATCH_ON_VERIFIED", "true").lower() in ("true", "1")

settings = Settings()

def reload_settings():
    load_dotenv(BASE_DIR / ".env", override=True)
    settings.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    settings.openai_api_key = os.getenv("OPENAI_API_KEY", "")
    settings.default_provider = os.getenv("DEFAULT_PROVIDER", "auto")
    settings.default_webhook_url = os.getenv("DEFAULT_WEBHOOK_URL", "")
    settings.webhook_secret = os.getenv("WEBHOOK_SECRET", "docflow_sec_default_change_me")
    settings.auto_dispatch_on_verified = os.getenv("AUTO_DISPATCH_ON_VERIFIED", "true").lower() in ("true", "1")
    return settings
