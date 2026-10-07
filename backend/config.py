import os
from pathlib import Path
from urllib.parse import quote_plus
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


def _database_url() -> str:
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url
    if os.getenv("DB_HOST") and os.getenv("DB_NAME"):
        user = quote_plus(os.getenv("DB_USER", ""))
        password = quote_plus(os.getenv("DB_PASSWORD", ""))
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        name = quote_plus(os.getenv("DB_NAME", "docflow"))
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    return f"sqlite:///{DB_PATH}"

class Settings(BaseModel):
    app_name: str = "DocFlow"
    app_version: str = "1.0.0"
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR
    uploads_dir: Path = UPLOADS_DIR
    db_path: Path = DB_PATH
    database_url: str = _database_url()
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "")
    secure_cookies: bool = os.getenv("SECURE_COOKIES", "false").lower() in ("true", "1")
    
    # AI Engine Keys
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    default_provider: str = os.getenv("DEFAULT_PROVIDER", "auto") # auto, gemini, openai, mistral, openrouter
    
    # Outbound Webhook config
    default_webhook_url: str = os.getenv("DEFAULT_WEBHOOK_URL", "")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    
    # Auto-dispatch flag: if True, send webhook immediately on successful VERIFIED parse
    auto_dispatch_on_verified: bool = os.getenv("AUTO_DISPATCH_ON_VERIFIED", "true").lower() in ("true", "1")

settings = Settings()

def reload_settings():
    load_dotenv(BASE_DIR / ".env", override=True)
    settings.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    settings.openai_api_key = os.getenv("OPENAI_API_KEY", "")
    settings.default_provider = os.getenv("DEFAULT_PROVIDER", "auto")
    settings.default_webhook_url = os.getenv("DEFAULT_WEBHOOK_URL", "")
    settings.webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    settings.auto_dispatch_on_verified = os.getenv("AUTO_DISPATCH_ON_VERIFIED", "true").lower() in ("true", "1")
    return settings
