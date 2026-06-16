"""
Investigation endpoint.

GET /api/v1/investigate/status
    Returns: Global data-availability status for the SystemStatus frontend component.
    Always returns HTTP 200 — never 500. On DB/internal failure, returns degraded
    status so the frontend fetchError state is not triggered.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.services.state_builder import get_system_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/investigate", tags=["investigation"])


# ── GET /api/v1/investigate/status ────────────────────────────────────────────


@router.get("/status")
async def system_status(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return global data-availability status and LLM configuration.

    Polled by the SystemStatus component on mount and every 30 s.
    Always returns HTTP 200 — internal errors are surfaced in the response
    body so the frontend never enters the 'Could not reach' error state.
    """
    try:
        base = await get_system_status(db)
    except Exception as exc:
        logger.error("get_system_status failed: %s", exc, exc_info=True)
        # Return a degraded-but-valid 200 so the frontend does not set fetchError.
        base = {
            "checkpoints": {},
            "status_error": str(exc),
        }

    base["llm"] = {
        "provider": "OpenRouter",
        "model": settings.LLM_MODEL,
        "api_key_configured": bool(settings.OPENROUTER_API_KEY),
        "timeout_s": settings.LLM_TIMEOUT,
    }
    return base
