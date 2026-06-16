"""
Anomaly Agent — deterministic threshold-based anomaly detection.

Input  : state.sensor_readings
Output : AgentResult.data = {
    "anomaly_detected": bool,
    "severity": "normal" | "warning" | "critical",
    "anomalies": [
        {
            "sensor": str,
            "value": float,
            "threshold": float,
            "level": "warning" | "critical",
            "deviation_percent": float,
            "detected_at": str   # ISO 8601
        }
    ]
}
"""

from typing import Any

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState

# Industrial sensor thresholds (SI units)
# temperature : °C          vibration : mm/s RMS
# pressure    : bar         load      : % (0–100)
# rpm         : rev/min
_THRESHOLDS: dict[str, dict[str, float]] = {
    "temperature": {"warning": 70.0,  "critical": 85.0},
    "vibration":   {"warning": 6.0,   "critical": 10.0},
    "pressure":    {"warning": 6.5,   "critical": 8.5},
    "load":        {"warning": 78.0,  "critical": 92.0},
    "rpm":         {"warning": 3200.0,"critical": 3600.0},
}

_SEVERITY_ORDER = {"normal": 0, "warning": 1, "critical": 2}


def run(state: VulcanOpsState) -> AgentResult:
    if not state.sensor_readings:
        return AgentResult(
            status="error",
            data={},
            errors=["No sensor readings available for anomaly detection"],
        )

    # Use the most recent reading as current operational state
    latest = max(state.sensor_readings, key=lambda r: r.timestamp)

    anomalies: list[dict[str, Any]] = []
    severity = "normal"

    sensor_values: dict[str, float | None] = {
        "temperature": latest.temperature,
        "vibration":   latest.vibration,
        "pressure":    latest.pressure,
        "load":        latest.load,
        "rpm":         latest.rpm,
    }

    for sensor, value in sensor_values.items():
        if value is None:
            continue

        thresholds = _THRESHOLDS[sensor]
        level: str | None = None

        if value >= thresholds["critical"]:
            level = "critical"
        elif value >= thresholds["warning"]:
            level = "warning"

        if level is not None:
            breach_threshold = thresholds[level]
            deviation = ((value - breach_threshold) / breach_threshold) * 100.0
            anomalies.append(
                {
                    "sensor": sensor,
                    "value": round(value, 4),
                    "threshold": breach_threshold,
                    "level": level,
                    "deviation_percent": round(deviation, 2),
                    "detected_at": latest.timestamp.isoformat(),
                }
            )
            if _SEVERITY_ORDER.get(level, 0) > _SEVERITY_ORDER.get(severity, 0):
                severity = level

    return AgentResult(
        status="success",
        data={
            "anomaly_detected": len(anomalies) > 0,
            "severity": severity,
            "anomalies": anomalies,
        },
    )
