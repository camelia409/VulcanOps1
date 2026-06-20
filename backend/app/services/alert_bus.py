"""
AlertBus — in-process asyncio pub/sub for real-time WebSocket alerts.

Architecture note
-----------------
This implementation uses a module-level singleton backed by Python asyncio
queues. It is correct for a single-worker deployment (Uvicorn with 1 worker,
which is this project's deployment model).

For multi-worker production (Gunicorn with multiple Uvicorn workers, or
Kubernetes with multiple pods), replace with Redis pub/sub or NATS:

  publisher:  redis.client.pubsub().publish(channel, json.dumps(alert))
  subscriber: redis.asyncio.client.PubSub.subscribe(channel)

This limitation is intentional: adding Redis as a dependency for a
single-machine demo would be premature complexity.

Alert persistence
-----------------
Alerts are NOT persisted to the database. A client that is offline when
an alert fires will miss it. If persistence is needed (audit log, replay),
add an alert_log table and write to it inside publish(). Out of scope here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_MAX = 100
_TARGET_ROLES = {"engineer", "supervisor", "manager"}


class AlertBus:
    """
    Fan-out pub/sub bus keyed by role.

    publish()      — sync, put_nowait so callers never block the event loop.
    subscribe()    — returns an asyncio.Queue the WebSocket handler reads.
    unsubscribe()  — removes and drains the queue; MUST be called on disconnect.
    """

    def __init__(self) -> None:
        # role → set of active queues
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)

    # ------------------------------------------------------------------ pub

    def publish(self, alert: dict[str, Any]) -> None:
        """
        Fan the alert out to all subscribers whose role is in alert["target_roles"].

        Uses put_nowait() so agent code can call this without awaiting.
        If a consumer queue is full (slow client), drop the oldest message and
        log a warning — we never block the publisher.
        """
        target_roles: list[str] = alert.get("target_roles", [])
        for role in target_roles:
            for q in list(self._queues.get(role, set())):
                if q.full():
                    try:
                        q.get_nowait()  # drop oldest
                    except asyncio.QueueEmpty:
                        pass
                    logger.warning(
                        "alert_bus: queue full for role=%s, dropped oldest alert", role
                    )
                try:
                    q.put_nowait(alert)
                except asyncio.QueueFull:
                    logger.warning(
                        "alert_bus: put_nowait failed for role=%s (race on full)", role
                    )

    # ------------------------------------------------------------------ sub

    def subscribe(self, role: str) -> asyncio.Queue:
        """Create and register a new queue for the given role."""
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._queues[role].add(q)
        return q

    def unsubscribe(self, role: str, queue: asyncio.Queue) -> None:
        """Remove queue and drain any pending messages."""
        self._queues[role].discard(queue)
        # Drain so GC can collect the queue immediately
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ------------------------------------------------------------------ helpers

    @property
    def subscriber_count(self) -> dict[str, int]:
        return {role: len(qs) for role, qs in self._queues.items() if qs}


# Module-level singleton — matches the pattern used by llm_service.
# main.py also stores this on app.state for FastAPI dependency access,
# but agent code (in graph_builder.py) imports it directly to avoid
# needing a Request object.
alert_bus = AlertBus()


def get_alert_bus() -> AlertBus:
    """Return the module-level AlertBus singleton."""
    return alert_bus


# ── alert factory helpers ──────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_critical_anomaly_alert(
    *,
    machine_id: str,
    machine_name: str | None,
    sensor: str,
    value: float,
    deviation_percent: float,
) -> dict[str, Any]:
    return {
        "alert_id": str(uuid.uuid4()),
        "type": "critical_anomaly",
        "severity": "critical",
        "machine_id": machine_id,
        "machine_name": machine_name,
        "title": f"Critical anomaly on {machine_name or machine_id}",
        "detail": (
            f"{sensor}={value:.2f}, {deviation_percent:.1f}% above critical threshold"
        ),
        "target_roles": ["engineer", "supervisor", "manager"],
        "created_at": _now_iso(),
        "links": {"report_batch_id": None, "feedback_id": None},
    }


def make_low_rul_alert(
    *,
    machine_id: str,
    machine_name: str | None,
    hours_remaining: float,
    basis: str,
) -> dict[str, Any]:
    return {
        "alert_id": str(uuid.uuid4()),
        "type": "low_rul",
        "severity": "high",
        "machine_id": machine_id,
        "machine_name": machine_name,
        "title": f"Low RUL: {machine_name or machine_id}",
        "detail": f"Estimated {hours_remaining:.0f}h remaining (basis: {basis})",
        "target_roles": ["engineer", "supervisor", "manager"],
        "created_at": _now_iso(),
        "links": {"report_batch_id": None, "feedback_id": None},
    }


def make_high_risk_job_alert(
    *,
    machine_id: str,
    machine_name: str | None,
    risk_level: str,
    root_cause: str,
    recommended_action: str,
    report_batch_id: str | None = None,
) -> dict[str, Any]:
    return {
        "alert_id": str(uuid.uuid4()),
        "type": "high_risk_job",
        "severity": risk_level,
        "machine_id": machine_id,
        "machine_name": machine_name,
        "title": f"High-risk diagnosis: {machine_name or machine_id}",
        "detail": (
            f"{root_cause} | recommended: {recommended_action[:80]}"
            + ("..." if len(recommended_action) > 80 else "")
        ),
        "target_roles": ["supervisor", "manager"],
        "created_at": _now_iso(),
        "links": {"report_batch_id": report_batch_id, "feedback_id": None},
    }


def make_contested_diagnosis_alert(
    *,
    machine_id: str,
    machine_name: str | None,
    reported_root_cause: str | None,
    actual_root_cause: str | None,
    feedback_id: str,
) -> dict[str, Any]:
    return {
        "alert_id": str(uuid.uuid4()),
        "type": "contested_diagnosis",
        "severity": "medium",
        "machine_id": machine_id,
        "machine_name": machine_name,
        "title": f"Diagnosis contested on {machine_name or machine_id}",
        "detail": (
            f"Reported '{reported_root_cause or 'unknown'}', "
            f"engineer says '{actual_root_cause or 'unknown'}'"
        ),
        "target_roles": ["engineer", "supervisor"],
        "created_at": _now_iso(),
        "links": {"report_batch_id": None, "feedback_id": feedback_id},
    }
