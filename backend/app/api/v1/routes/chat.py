"""
Chat endpoint — Industrial Copilot entry point.

POST /api/v1/chat
    Input : { "query": str, "session_context": { "last_machine_id": str|null, "last_intent": str|null } }
    Output: copilot response (structured, from report cache — never re-runs agents)

GET /api/v1/chat/plant-overview
    Output: { total_machines, emergency_count, urgent_count, routine_count,
              full_ai_count, fast_count, error_count, last_processed }

GET /api/v1/chat/history
    Output: { "messages": [...] }

Flow:
    query → query_router.route() → execute_from_intent(routing, query, db, session_context)

This file is an HTTP adapter only. No business logic, no machine selection,
no pipeline execution.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.chat_message import ChatMessage
from app.services import query_router
from app.services.integration_service import execute_from_intent, get_plant_overview

router = APIRouter(prefix="/chat", tags=["chat"])


class SessionContext(BaseModel):
    last_machine_id: str | None = None
    last_intent: str | None = None


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    session_context: SessionContext = Field(default_factory=SessionContext)


@router.post("")
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Industrial Copilot — natural language query interface.

    Reads from the report cache (report_batches). Never re-runs agents.
    Session context enables pronoun resolution across turns.
    """
    routing = query_router.route(body.query)

    try:
        response = await execute_from_intent(
            routing,
            body.query,
            db,
            session_context={
                "last_machine_id": body.session_context.last_machine_id,
                "last_intent": body.session_context.last_intent,
            },
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    db.add(ChatMessage(role="user", query=body.query))
    db.add(ChatMessage(role="assistant", query=body.query, response_json=response))
    await db.commit()

    return response


@router.get("/plant-overview")
async def plant_overview_endpoint(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate plant statistics from all latest cached reports. No LLM."""
    return await get_plant_overview(db)


@router.get("/history")
async def chat_history(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return recent chat turns for the multi-turn conversation panel."""
    result = await db.execute(
        select(ChatMessage)
        .order_by(ChatMessage.created_at.asc())
        .limit(max(1, min(limit, 200)))
    )
    rows = list(result.scalars().all())

    return {
        "messages": [
            {
                "message_id": str(m.message_id),
                "role": m.role,
                "query": m.query,
                "response_json": m.response_json,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }
