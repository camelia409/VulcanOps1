# Defensive shim: some langchain / langchain-core combinations check for
# _HAS_LANGCHAIN=True and then read langchain.debug / .verbose / .llm_cache.
# If the installed langchain package is missing those attributes (e.g. a stub
# or empty __init__.py), inject defaults before any langgraph import resolves
# them and triggers an AttributeError.
try:
    import langchain as _lc
except ImportError:
    _lc = None
if _lc is not None:
    for _attr, _default in (("debug", False), ("verbose", False), ("llm_cache", None)):
        if not hasattr(_lc, _attr):
            setattr(_lc, _attr, _default)
    del _lc, _attr, _default
else:
    del _lc

from contextlib import asynccontextmanager
from pathlib import Path

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.api.v1.router import api_router
from app.db.session import engine
from app.db.base import Base

# Import all models to register them with Base.metadata before create_all()
from app.models import (
    ChatMessage,
    IngestedFile,
    IngestionEvent,
    Machine,
    MaintenanceRecord,
    ReportBatch,
    SensorReading,
    StoredRoleReport,
)


def _ensure_storage_dirs() -> None:
    """Create local storage directories if they do not exist."""
    # app/main.py is at backend/app/main.py; storage lives at backend/storage.
    storage_root = Path(__file__).resolve().parents[1] / "storage"
    subdirs = [
        "uploads",
        "uploads/manuals",
        "uploads/sops",
        "uploads/machine_registry",
        "uploads/sensor_history",
        "uploads/maintenance_history",
        "pdfs",
        "documents",
    ]
    for sub in subdirs:
        (storage_root / sub).mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database schema and storage directories on startup."""
    _ensure_storage_dirs()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Cleanup on shutdown
    await engine.dispose()


logger = logging.getLogger(__name__)

app = FastAPI(
    title="VulcanOps",
    version="0.1.0",
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.APP_ENV != "production" else None,
    lifespan=lifespan,
)

_origins = settings.allowed_origins_list
logger.info("CORS allowed origins: %s", _origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/ready")
async def ready():
    """Readiness probe for Render / container orchestrators."""
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    influx_ok = bool(settings.INFLUX_URL or (
        settings.INFLUXDB_HOST and settings.INFLUXDB_TOKEN != "change-me-in-production"
    ))

    openrouter_ok = bool(
        settings.OPENROUTER_API_KEY
        and settings.OPENROUTER_API_KEY != "change-me-in-production"
        and settings.OPENROUTER_BASE_URL
    )

    return {
        "database": db_ok,
        "influx": influx_ok,
        "openrouter": openrouter_ok,
    }
