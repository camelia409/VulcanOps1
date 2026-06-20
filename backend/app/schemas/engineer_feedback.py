from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class FeedbackCreate(BaseModel):
    thumbs: str | None = None
    verdict: str | None = None
    actual_root_cause: str | None = None
    notes: str | None = None
    engineer_id: str | None = None

    @field_validator("thumbs")
    @classmethod
    def validate_thumbs(cls, v: str | None) -> str | None:
        if v is not None and v not in ("up", "down"):
            raise ValueError("thumbs must be 'up' or 'down'")
        return v

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str | None) -> str | None:
        if v is not None and v not in ("correct", "partial", "wrong"):
            raise ValueError("verdict must be 'correct', 'partial', or 'wrong'")
        return v


class FeedbackSchema(BaseModel):
    feedback_id: uuid.UUID
    report_batch_id: uuid.UUID
    machine_id: uuid.UUID
    failure_mode: str | None = None
    reported_root_cause: str | None = None
    thumbs: str | None = None
    verdict: str | None = None
    actual_root_cause: str | None = None
    notes: str | None = None
    engineer_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
