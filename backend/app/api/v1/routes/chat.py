"""
Chat endpoint — Industrial Copilot entry point.

POST /api/v1/chat
    Input : { "query": str, "session_id"?: str, "session_context"?: {...} }
    Output:
        If session_id provided (new multi-turn mode):
            { session_id, status, answer, clarification_question, routing_intent, resolved_query }
        If no session_id (legacy mode, backwards-compatible):
            legacy response from execute_from_intent

GET /api/v1/chat/plant-overview
    Output: { total_machines, emergency_count, ... }

GET /api/v1/chat/history
    Output: { "messages": [...] }

Flow (with session_id):
    query → LangGraph chat graph → checkpoint → response
    If needs_clarification: returns status="needs_clarification" + clarification_question
    Next turn with same session_id: resumes from checkpoint (HITL pattern)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langchain_core.messages import HumanMessage
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
    session_id: str | None = None
    session_context: SessionContext = Field(default_factory=SessionContext)


# ---------------------------------------------------------------------------
# Multi-turn (session-aware) handler
# ---------------------------------------------------------------------------

async def _chat_with_session(
    query: str,
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """
    Run query through the LangGraph chat graph with persistent session memory.

    Detects pending HITL interrupts and resumes automatically when the user
    sends a clarification answer.
    """
    graph = request.app.state.chat_graph
    checkpointer = request.app.state.chat_checkpointer

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": session_id,
            "checkpoint_ns": "",
        }
    }

    # Check whether a previous turn is awaiting clarification
    saved = await checkpointer.aget_tuple(
        {"configurable": {"thread_id": session_id, "checkpoint_ns": ""}}
    )
    is_awaiting_clarification = False
    if saved:
        channel_values = saved.checkpoint.get("channel_values", {})
        is_awaiting_clarification = bool(channel_values.get("needs_clarification"))

    if is_awaiting_clarification:
        # Inject the new message as the clarification answer and resume
        await graph.aupdate_state(config, {"messages": [HumanMessage(query)]})
        state = await graph.ainvoke(None, config)
    else:
        state = await graph.ainvoke(
            {"messages": [HumanMessage(query)]}, config
        )

    if state.get("needs_clarification"):
        return {
            "session_id": session_id,
            "status": "needs_clarification",
            "answer": None,
            "clarification_question": state.get("clarification_question"),
            "routing_intent": state.get("routing_intent"),
            "resolved_query": query,
        }

    return {
        "session_id": session_id,
        "status": "answered",
        "answer": state.get("answer"),
        "clarification_question": None,
        "routing_intent": state.get("routing_intent"),
        "resolved_query": state.get("resolved_query") or query,
    }


# ---------------------------------------------------------------------------
# Legacy (stateless) handler — kept for backwards compat
# ---------------------------------------------------------------------------

async def _chat_legacy(
    query: str,
    session_context: SessionContext,
    db: AsyncSession,
) -> dict[str, Any]:
    routing = query_router.route(query)
    try:
        return await execute_from_intent(
            routing,
            query,
            db,
            session_context={
                "last_machine_id": session_context.last_machine_id,
                "last_intent": session_context.last_intent,
            },
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("")
async def chat(
    body: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Industrial Copilot — natural language query interface.

    Supply a session_id to enable multi-turn memory and HITL clarification.
    Omit session_id for the legacy stateless mode (backwards-compatible).
    """
    if body.session_id is not None:
        # Multi-turn mode: use LangGraph graph + persistent checkpointer
        response = await _chat_with_session(body.query, body.session_id, request)
    else:
        # Legacy mode: stateless, no session memory
        response = await _chat_legacy(body.query, body.session_context, db)

    # Persist to chat_messages regardless of mode
    db.add(ChatMessage(role="user", query=body.query))
    db.add(ChatMessage(
        role="assistant",
        query=body.query,
        response_json=response,
    ))
    await db.commit()

    return response


# ---------------------------------------------------------------------------
# Utility endpoints (unchanged)
# ---------------------------------------------------------------------------

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
