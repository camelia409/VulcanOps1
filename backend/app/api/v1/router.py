from fastapi import APIRouter

from app.api.v1.routes.chat import router as chat_router
from app.api.v1.routes.ingest import router as ingest_router
from app.api.v1.routes.investigation import router as investigation_router
from app.api.v1.routes.reports import router as reports_router
from app.api.v1.routes.upload import router as upload_router

api_router = APIRouter()
api_router.include_router(upload_router)
api_router.include_router(chat_router)
api_router.include_router(investigation_router)
api_router.include_router(ingest_router)
api_router.include_router(reports_router)
