"""
Prognostics Engine — deterministic Remaining Useful Life (RUL) estimation.

Input  : state.sensor_readings, state.maintenance_history
Output : AgentResult.data = {
    "hours_remaining": float,
    "confidence": float,        # 0.0 – 1.0
    "basis": str,               # which sensor drove the estimate
    "sensor_trends": {          # per-sensor slope and projected breach
        "<sensor>": {
            "slope_per_hour": float,
            "current_value": float,
            "critical_threshold": float,
            "hours_to_breach": float | None
        }
    }
}

Method: linear regression on timestamped readings, extrapolated to critical threshold.
Confidence is derived from R² and data density.
"""

from typing import Any

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState

_CRITICAL_THRESHOLDS: dict[str, float] = {
    "temperature": 85.0,
    "vibration":   10.0,
    "pressure":    8.5,
    "load":        92.0,
    "rpm":         3600.0,
}

# Minimum readings required to attempt linear regression
_MIN_READINGS = 3

# Default RUL when no degradation trend is detected (hours)
_DEFAULT_RUL = 720.0  # 30 days


def _linear_regression(
    x: list[float], y: list[float]
) -> tuple[float, float, float]:
    """
    Returns (slope, intercept, r_squared).
    x values should be elapsed hours from first reading.
    """
    n = len(x)
    if n < 2:
        return 0.0, y[0] if y else 0.0, 0.0

    x_mean = sum(x) / n
    y_mean = sum(y) / n

    ss_xy = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    ss_xx = sum((xi - x_mean) ** 2 for xi in x)

    if ss_xx == 0.0:
        return 0.0, y_mean, 0.0

    slope = ss_xy / ss_xx
    intercept = y_mean - slope * x_mean

    y_pred = [slope * xi + intercept for xi in x]
    ss_res = sum((yi - yp) ** 2 for yi, yp in zip(y, y_pred))
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return slope, intercept, max(0.0, min(1.0, r_squared))


def run(state: VulcanOpsState) -> AgentResult:
    if not state.sensor_readings:
        return AgentResult(
            status="error",
            data={},
            errors=["No sensor readings available for prognostics"],
        )

    if len(state.sensor_readings) < _MIN_READINGS:
        return AgentResult(
            status="error",
            data={},
            errors=[
                f"Insufficient data: prognostics requires at least {_MIN_READINGS} "
                f"readings, got {len(state.sensor_readings)}"
            ],
        )

    readings = sorted(state.sensor_readings, key=lambda r: r.timestamp)
    t0 = readings[0].timestamp

    # Build time axis in hours from first reading
    hours: list[float] = [
        (r.timestamp - t0).total_seconds() / 3600.0 for r in readings
    ]

    sensor_fields: dict[str, list[float | None]] = {
        "temperature": [r.temperature for r in readings],
        "vibration":   [r.vibration   for r in readings],
        "pressure":    [r.pressure    for r in readings],
        "load":        [r.load        for r in readings],
        "rpm":         [r.rpm         for r in readings],
    }

    sensor_trends: dict[str, Any] = {}
    rul_candidates: list[tuple[float, float, str]] = []  # (hours_to_breach, r_sq, sensor)

    for sensor, raw_values in sensor_fields.items():
        threshold = _CRITICAL_THRESHOLDS[sensor]

        # Drop None entries, keeping paired (hour, value) tuples
        pairs = [(h, v) for h, v in zip(hours, raw_values) if v is not None]
        if len(pairs) < _MIN_READINGS:
            continue

        x_vals = [p[0] for p in pairs]
        y_vals = [p[1] for p in pairs]

        slope, intercept, r_sq = _linear_regression(x_vals, y_vals)
        current = y_vals[-1]

        hours_to_breach: float | None = None
        if slope > 0 and current < threshold:
            # Project forward: threshold = slope * t + intercept
            t_breach = (threshold - intercept) / slope
            elapsed = x_vals[-1]
            remaining = t_breach - elapsed
            if remaining > 0:
                hours_to_breach = round(remaining, 1)
                rul_candidates.append((remaining, r_sq, sensor))

        sensor_trends[sensor] = {
            "slope_per_hour": round(slope, 6),
            "current_value": round(current, 4),
            "critical_threshold": threshold,
            "hours_to_breach": hours_to_breach,
        }

    if not rul_candidates:
        # No sensor is trending toward threshold breach
        return AgentResult(
            status="success",
            data={
                "hours_remaining": _DEFAULT_RUL,
                "confidence": 0.5,
                "basis": "No degradation trend detected in any sensor",
                "sensor_trends": sensor_trends,
            },
        )

    # RUL = minimum hours to any threshold breach; confidence from that sensor's R²
    rul_candidates.sort(key=lambda t: t[0])
    hours_remaining, r_sq, limiting_sensor = rul_candidates[0]

    # Scale confidence: high R² + many readings = high confidence
    data_quality = min(1.0, len(readings) / 20.0)
    confidence = round(r_sq * 0.7 + data_quality * 0.3, 3)

    # Factor in historical MTBF if available
    if state.maintenance_history:
        avg_downtime = sum(r.downtime_hours for r in state.maintenance_history) / len(
            state.maintenance_history
        )
        # If RUL < historical average downtime, flag lower confidence
        if hours_remaining < avg_downtime:
            confidence = round(confidence * 0.85, 3)

    return AgentResult(
        status="success",
        data={
            "hours_remaining": round(hours_remaining, 1),
            "confidence": min(1.0, confidence),
            "basis": f"Linear extrapolation of {limiting_sensor} to critical threshold {_CRITICAL_THRESHOLDS[limiting_sensor]}",
            "sensor_trends": sensor_trends,
        },
    )
