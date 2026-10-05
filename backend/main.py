import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.config import settings, BASE_DIR, UPLOADS_DIR
from backend.database import init_db, DocumentRepository
from backend.api import documents_router, inbound_router, settings_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure SQLite database and directories are initialized
    init_db()
    yield

app = FastAPI(
    title="DocFlow - Headless Document & PO to Webhook Engine",
    description="High-precision B2B document extraction with automated arithmetic validation and webhook dispatch.",
    version="1.0.0",
    lifespan=lifespan
)

# Static and Upload mounts
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# Templates
templates_dir = BASE_DIR / "templates"
templates_dir.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

# Include API Routers
app.include_router(documents_router)
app.include_router(inbound_router)
app.include_router(settings_router)

# Frontend UI Routes
@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    docs = DocumentRepository.list_all(limit=50)
    stats = DocumentRepository.get_stats()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "docs": docs,
            "stats": stats,
            "app_name": settings.app_name,
            "settings": settings
        }
    )

@app.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_detail_view(request: Request, doc_id: str):
    doc = DocumentRepository.get_by_id(doc_id)
    if not doc:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        request,
        "document_detail.html",
        {
            "doc": doc,
            "app_name": settings.app_name,
            "settings": settings
        }
    )

@app.get("/settings-view", response_class=HTMLResponse)
async def settings_view(request: Request):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "app_name": settings.app_name,
            "settings": settings
        }
    )

@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}
