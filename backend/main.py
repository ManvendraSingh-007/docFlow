import os
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from backend.config import settings, BASE_DIR, UPLOADS_DIR
from backend.database import init_db, DocumentRepository, WebhookLogRepository, AccountSettingsRepository, ProcessingRunRepository
from backend.api import documents_router, inbound_router, settings_router
from backend.api.auth import router as auth_router
from backend.auth import csrf_token_for, user_for_page
from backend.security import get_app_secret

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure SQLite database and directories are initialized
    init_db()
    from backend.services.processing_queue import processing_worker
    worker_task = asyncio.create_task(processing_worker())
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="DocFlow",
    description="Invoice intake and review.",
    version="1.0.0",
    lifespan=lifespan
)
app.add_middleware(
    SessionMiddleware,
    secret_key=get_app_secret(),
    session_cookie="docflow_session",
    https_only=settings.secure_cookies,
    same_site="lax",
    max_age=60 * 60 * 12,
)

# Static assets
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Templates
templates_dir = BASE_DIR / "templates"
templates_dir.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

# Include API Routers
app.include_router(documents_router)
app.include_router(inbound_router)
app.include_router(settings_router)
app.include_router(auth_router)

# Frontend UI Routes
@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    user = user_for_page(request)
    if not user:
        return templates.TemplateResponse(
            request,
            "landing.html",
            {"app_name": settings.app_name, "csrf_token": csrf_token_for(request)},
        )
    docs = DocumentRepository.list_all(limit=50, user_id=user["id"])
    stats = DocumentRepository.get_stats(user_id=user["id"])
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "docs": docs,
            "stats": stats,
            "app_name": settings.app_name,
            "settings": settings,
            "current_user": user,
            "csrf_token": csrf_token_for(request),
        }
    )

@app.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_detail_view(request: Request, doc_id: str):
    user = user_for_page(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    doc = DocumentRepository.get_by_id(doc_id, user_id=user["id"])
    if not doc:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        request,
        "document_detail.html",
        {
            "doc": doc,
            "app_name": settings.app_name,
            "settings": settings,
            "current_user": user,
            "csrf_token": csrf_token_for(request),
            "webhook_logs": WebhookLogRepository.list_by_document(doc_id),
            "processing_runs": ProcessingRunRepository.list_for_document(doc_id, user["id"]),
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    return RedirectResponse("/settings/account", status_code=303)

@app.get("/settings/{section}", response_class=HTMLResponse)
async def settings_section_view(request: Request, section: str):
    user = user_for_page(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if section not in {"account", "security", "extraction", "integrations"}:
        return RedirectResponse("/settings/account", status_code=303)
    account_settings = AccountSettingsRepository.get(user["id"])
    return templates.TemplateResponse(
        request,
        "account_settings.html",
        {
            "app_name": settings.app_name,
            "settings": settings,
            "current_user": user,
            "account_settings": account_settings,
            "settings_section": section,
            "csrf_token": csrf_token_for(request),
        }
    )

@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}
