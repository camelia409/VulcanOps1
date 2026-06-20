"""
LangGraph-based chat graph with:
  - Persistent session memory (VulcanOpsCheckpointer)
  - HITL clarification for ambiguous / pronoun-only queries
  - Automatic pronoun resolution when prior context exists

Graph topology
--------------
  START → analyze → (conditional) → request_clarification* → answer → END
                                  → answer → END

  * interrupt_before=["request_clarification"] pauses here so the caller can
    return a clarification_question to the user. On the next turn the caller
    calls aupdate_state() + ainvoke(None, ...) to resume.

Node signatures follow the LangGraph 0.2.x convention:
  async def node(state: ChatState, config: RunnableConfig) -> dict
"""

from __future__ import annotations

# Patch stub langchain package before LangGraph imports access its attributes
try:
    import langchain as _lc
    for _a, _d in (("debug", False), ("verbose", False), ("llm_cache", None)):
        if not hasattr(_lc, _a):
            setattr(_lc, _a, _d)
    del _lc, _a, _d
except ImportError:
    pass

import logging
import re
from typing import Annotated, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    original_query: str
    resolved_query: str
    routing_intent: str | None
    answer: dict | None
    clarification_question: str | None
    needs_clarification: bool
    last_machine_id: str | None


# ---------------------------------------------------------------------------
# Ambiguity detection helpers
# ---------------------------------------------------------------------------

_PRONOUN_RE = re.compile(
    r"\b(it|its|the machine|this machine|that machine|same machine)\b", re.I
)
_MACHINE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _resolve_pronouns(query: str, machine_id: str) -> str:
    """Replace vague references with the machine_id we already know about."""
    return _PRONOUN_RE.sub(f"machine {machine_id}", query)


def _extract_machine_id_from_messages(messages: list[BaseMessage]) -> str | None:
    """Scan AI messages for a machine_id injected as additional_kwargs."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            mid = msg.additional_kwargs.get("machine_id")
            if mid:
                return mid
    return None


# ---------------------------------------------------------------------------
# Node: analyze
# ---------------------------------------------------------------------------

async def _analyze_node(state: ChatState, config: RunnableConfig) -> dict:
    """
    Classifies the incoming query.
    Sets needs_clarification=True (+ clarification_question) for ambiguous queries,
    or resolves pronouns and sets resolved_query for clear ones.
    """
    from app.services import query_router  # local import avoids circular deps

    messages: list[BaseMessage] = state.get("messages", [])
    human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    if not human_msgs:
        return {
            "original_query": "",
            "resolved_query": "",
            "needs_clarification": False,
        }

    query = human_msgs[-1].content
    last_machine_id: str | None = state.get("last_machine_id") or _extract_machine_id_from_messages(messages)

    has_pronoun = bool(_PRONOUN_RE.search(query))
    routing = query_router.route(query)

    if has_pronoun and not last_machine_id:
        return {
            "original_query": query,
            "resolved_query": query,
            "needs_clarification": True,
            "clarification_question": (
                "Which machine are you referring to? "
                "Please specify the machine name or ID."
            ),
            "routing_intent": routing.intent,
            "last_machine_id": None,
        }

    resolved = _resolve_pronouns(query, last_machine_id) if (has_pronoun and last_machine_id) else query
    return {
        "original_query": query,
        "resolved_query": resolved,
        "needs_clarification": False,
        "clarification_question": None,
        "routing_intent": routing.intent,
        "last_machine_id": last_machine_id,
    }


# ---------------------------------------------------------------------------
# Node: request_clarification  (interrupted before execution)
# ---------------------------------------------------------------------------

async def _request_clarification_node(state: ChatState, config: RunnableConfig) -> dict:
    """
    Runs AFTER the interrupt is resumed — the last HumanMessage is the user's answer.
    Rebuilds resolved_query and re-routes.
    """
    from app.services import query_router

    messages: list[BaseMessage] = state.get("messages", [])
    human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    clarification = human_msgs[-1].content if human_msgs else ""
    original = state.get("original_query", "")

    resolved = f"{original} (clarification: {clarification})"

    # If the user typed a UUID, treat it as the machine they meant
    new_machine_id = state.get("last_machine_id")
    if _MACHINE_ID_RE.match(clarification.strip()):
        new_machine_id = clarification.strip()
        resolved = _resolve_pronouns(original, new_machine_id)

    routing = query_router.route(resolved)

    return {
        "resolved_query": resolved,
        "routing_intent": routing.intent,
        "needs_clarification": False,
        "clarification_question": None,
        "last_machine_id": new_machine_id,
    }


# ---------------------------------------------------------------------------
# Node: answer
# ---------------------------------------------------------------------------

async def _execute_query_node(state: ChatState, config: RunnableConfig) -> dict:
    """Execute the resolved query via the existing integration service."""
    from app.db.session import AsyncSessionLocal
    from app.services import query_router
    from app.services.integration_service import execute_from_intent

    resolved_query = state.get("resolved_query") or state.get("original_query", "")
    last_machine_id = state.get("last_machine_id")

    routing = query_router.route(resolved_query)

    try:
        async with AsyncSessionLocal() as db:
            answer = await execute_from_intent(
                routing,
                resolved_query,
                db,
                session_context={
                    "last_machine_id": last_machine_id,
                    "last_intent": state.get("routing_intent"),
                },
            )
    except Exception as exc:
        logger.error("execute_from_intent failed: %s", exc)
        answer = {
            "error": str(exc),
            "title": "Error",
            "intent": routing.intent,
            "reports": [],
            "report_count": 0,
        }

    # Extract machine_id from the response for the next turn
    new_machine_id = last_machine_id
    reports = answer.get("reports", [])
    if reports:
        mid = reports[0].get("machine", {}).get("machine_id")
        if mid:
            new_machine_id = mid

    return {
        "answer": answer,
        "routing_intent": routing.intent,
        "last_machine_id": new_machine_id,
    }


# ---------------------------------------------------------------------------
# Conditional edge
# ---------------------------------------------------------------------------

def _route_after_analyze(state: ChatState) -> str:
    return "request_clarification" if state.get("needs_clarification") else "execute_query"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_chat_graph(checkpointer: BaseCheckpointSaver) -> Any:
    """Compile the chat graph with the given checkpointer."""
    builder: StateGraph = StateGraph(ChatState)

    builder.add_node("analyze", _analyze_node)
    builder.add_node("request_clarification", _request_clarification_node)
    builder.add_node("execute_query", _execute_query_node)

    builder.set_entry_point("analyze")
    builder.add_conditional_edges("analyze", _route_after_analyze)
    builder.add_edge("request_clarification", "execute_query")
    builder.add_edge("execute_query", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["request_clarification"],
    )
