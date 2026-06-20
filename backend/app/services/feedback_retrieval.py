"""Retrieve signal-bearing engineer feedback relevant to an ongoing diagnosis.

Relevance ranking (no embeddings — heuristic priority):
  Tier 1: same machine_id AND same failure_mode
  Tier 2: same failure_mode on any machine
  Tier 3: same machine_id with any failure_mode

Only signal-bearing rows are returned:
  verdict='wrong' | 'partial', OR thumbs='down', OR notes is non-empty.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select

from app.db.session import AsyncSessionLocal
from app.models.engineer_feedback import EngineerFeedback


async def get_relevant_feedback(
    machine_id: uuid.UUID,
    failure_mode: str | None,
    limit: int = 5,
) -> list[dict]:
    """Return up to `limit` signal-bearing feedback rows ranked by relevance."""

    signal_filter = or_(
        EngineerFeedback.verdict.in_(["wrong", "partial"]),
        EngineerFeedback.thumbs == "down",
        and_(
            EngineerFeedback.notes.isnot(None),
            EngineerFeedback.notes != "",
        ),
    )

    seen: dict[uuid.UUID, dict] = {}

    async with AsyncSessionLocal() as db:
        # Tier 1 — same machine + same failure_mode
        if failure_mode:
            r1 = await db.execute(
                select(EngineerFeedback)
                .where(
                    EngineerFeedback.machine_id == machine_id,
                    EngineerFeedback.failure_mode.ilike(f"%{failure_mode}%"),
                    signal_filter,
                )
                .order_by(EngineerFeedback.created_at.desc())
                .limit(limit)
            )
            for row in r1.scalars().all():
                if row.feedback_id not in seen:
                    seen[row.feedback_id] = _row_to_dict(row)

        # Tier 2 — same failure_mode on any machine
        if failure_mode and len(seen) < limit:
            r2 = await db.execute(
                select(EngineerFeedback)
                .where(
                    EngineerFeedback.failure_mode.ilike(f"%{failure_mode}%"),
                    signal_filter,
                )
                .order_by(EngineerFeedback.created_at.desc())
                .limit(limit)
            )
            for row in r2.scalars().all():
                if row.feedback_id not in seen and len(seen) < limit:
                    seen[row.feedback_id] = _row_to_dict(row)

        # Tier 3 — same machine, any failure mode
        if len(seen) < limit:
            r3 = await db.execute(
                select(EngineerFeedback)
                .where(
                    EngineerFeedback.machine_id == machine_id,
                    signal_filter,
                )
                .order_by(EngineerFeedback.created_at.desc())
                .limit(limit)
            )
            for row in r3.scalars().all():
                if row.feedback_id not in seen and len(seen) < limit:
                    seen[row.feedback_id] = _row_to_dict(row)

    return list(seen.values())


def _row_to_dict(row: EngineerFeedback) -> dict:
    return {
        "feedback_id": str(row.feedback_id),
        "machine_id": str(row.machine_id),
        "failure_mode": row.failure_mode,
        "reported_root_cause": row.reported_root_cause,
        "thumbs": row.thumbs,
        "verdict": row.verdict,
        "actual_root_cause": row.actual_root_cause,
        "notes": row.notes,
        "engineer_id": row.engineer_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
