"""
WebSocket alert endpoint — real-time push channel (PS §6.7).

GET  /api/v1/ws/alerts/{role}

role must be one of: engineer | supervisor | manager

The client receives a JSON frame for every alert whose target_roles
list contains the requested role.  The first frame is a "connected"
handshake so the client can confirm the channel is live.

TODO (production hardening):
  - Add bearer-token / session-cookie auth before accepting the socket.
    Currently role is self-declared by the client, which is fine for a
    demo but not for production. Pattern: read Authorization header in
    the handshake, validate JWT, extract role claim.
  - Rate-limit connections per IP to prevent queue exhaustion DoS.

Alert payload shape (see alert_bus.py make_*_alert helpers):
  {
    "alert_id": str,
    "type": "critical_anomaly"|"low_rul"|"high_risk_job"|"contested_diagnosis",
    "severity": "critical"|"high"|"medium",
    "machine_id": str,
    "machine_name": str | None,
    "title": str,
    "detail": str,
    "target_roles": [str, ...],
    "created_at": ISO 8601,
    "links": {"report_batch_id": str|None, "feedback_id": str|None}
  }
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.alert_bus import get_alert_bus

router = APIRouter(tags=["alerts"])

_VALID_ROLES = {"engineer", "supervisor", "manager"}


@router.websocket("/ws/alerts/{role}")
async def ws_alerts(websocket: WebSocket, role: str) -> None:
    if role not in _VALID_ROLES:
        await websocket.close(code=1008, reason="invalid role")
        return

    await websocket.accept()
    bus = get_alert_bus()
    queue = bus.subscribe(role)

    try:
        # Handshake frame — client uses this to confirm the channel is live
        await websocket.send_json({
            "type": "connected",
            "role": role,
            "server_time": datetime.now(timezone.utc).isoformat(),
        })

        while True:
            alert = await queue.get()
            await websocket.send_json(alert)

    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(role, queue)
