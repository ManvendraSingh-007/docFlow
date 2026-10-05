from .documents import router as documents_router
from .inbound_email import router as inbound_router
from .settings import router as settings_router

__all__ = ["documents_router", "inbound_router", "settings_router"]
