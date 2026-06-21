from typing import Any
from pydantic import BaseModel


class AgentResult(BaseModel):
    """
    Canonical output wrapper for every agent in the VulcanOps pipeline.

    status          : "success" | "degraded" | "error"
    data            : agent-specific output payload
    errors          : non-empty only when status == "error" or partial failures occurred
    degraded_reason : set when status == "degraded"; explains which fallback fired and why
    """

    status: str
    data: dict[str, Any]
    errors: list[str] = []
    degraded_reason: str | None = None
