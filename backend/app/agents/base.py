from typing import Any
from pydantic import BaseModel


class AgentResult(BaseModel):
    """
    Canonical output wrapper for every agent in the VulcanOps pipeline.

    status  : "success" | "error"
    data    : agent-specific output payload
    errors  : non-empty only when status == "error" or partial failures occurred
    """

    status: str
    data: dict[str, Any]
    errors: list[str] = []
