"""Tests for the report-quality matrix and finalization logic."""

import pytest

from app.orchestrator.graph_builder import (
    ReportDisposition,
    ReportQuality,
    _build_evidence_chain,
    _classify_report,
    _compute_explainability_score,
    _compute_procurement_gap,
    _sanitize_uncertain_text,
)
from app.services.report_builder import build_single_report


from app.core.state_contract import (
    AnomalyDetail,
    DiagnosisResult,
    ImpactAssessment,
    RULPrediction,
    StrategyDecision,
)
from app.schemas.maintenance_record import MaintenanceRecordSchema
from app.schemas.sensor_reading import SensorReadingSchema


def _make_state(
    confidence=0.8,
    verified=True,
    evidence_score=0.4,
    history_score=0.3,
    has_evidence=True,
    rul_hours=168.0,
):
    """Minimal VulcanOpsState for report builder tests."""
    import uuid
    from datetime import date, datetime, timezone

    from app.core.state_contract import VulcanOpsState

    return VulcanOpsState(
        active_machine_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        diagnosis=DiagnosisResult(
            root_cause="Bearing wear",
            failure_mode="Rolling element fatigue",
            confidence=confidence,
        ),
        strategy=StrategyDecision(
            recommended_action="Replace bearings",
            parts_required=["bearings"],
        ),
        impact=ImpactAssessment(),
        anomaly=AnomalyDetail(
            detected=True,
            sensor="vibration",
            value=8.7,
            threshold=4.5,
            deviation_percent=93.3,
            detected_at=datetime.now(timezone.utc),
        ),
        rul_prediction=RULPrediction(remaining_useful_life_hours=rul_hours),
        maintenance_history=[
            MaintenanceRecordSchema(
                maintenance_id=uuid.uuid4(),
                machine_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
                date=date(2024, 1, 15),
                failure_mode="Bearing fatigue",
                action_taken="Replaced bearings",
                downtime_hours=4.0,
                engineer="J. Smith",
            ),
        ],
        retrieved_evidence=[
            {
                "source": "compressor_manual.pdf",
                "source_type": "manual",
                "chunk": "Bearing wear causes vibration escalation above 6 mm/s.",
                "relevance_score": 0.85,
            }
        ] if has_evidence else [],
        final_report={
            "evidence_score": evidence_score,
            "history_score": history_score,
            "fallback_used": False,
            "uncertainty_reason": None,
            "final_report_status": "specific",
            "circuit_breaker_state": "CLOSED",
        },
    )


def test_classify_high_confidence_verified():
    disposition, reason = _classify_report(
        confidence=0.9, verified=True, evidence_score=0.4, history_score=0.3, has_evidence=True
    )
    assert disposition == ReportDisposition.SPECIFIC
    assert reason == "high_confidence_verified"


def test_classify_moderate_confidence_with_evidence():
    disposition, reason = _classify_report(
        confidence=0.75, verified=False, evidence_score=0.3, history_score=0.0, has_evidence=True
    )
    assert disposition == ReportDisposition.SPECIFIC
    assert reason == "moderate_confidence_supported"


def test_classify_low_confidence_partial_evidence():
    disposition, reason = _classify_report(
        confidence=0.55, verified=False, evidence_score=0.3, history_score=0.0, has_evidence=True
    )
    assert disposition == ReportDisposition.CAUTIOUS
    assert reason == "low_confidence_partial_evidence"


def test_classify_weak_evidence_present():
    disposition, reason = _classify_report(
        confidence=0.45, verified=False, evidence_score=0.15, history_score=0.0, has_evidence=True
    )
    assert disposition == ReportDisposition.CAUTIOUS
    assert reason == "evidence_present_but_weak"


def test_classify_insufficient_evidence():
    disposition, reason = _classify_report(
        confidence=0.45, verified=False, evidence_score=0.0, history_score=0.0, has_evidence=False
    )
    assert disposition == ReportDisposition.FALLBACK
    assert reason == "insufficient_evidence"


def test_sanitize_uncertain_text_strips_certainty_claims():
    raw = "The root cause is confirmed to be bearing failure and is certainly supported by available evidence."
    sanitized = _sanitize_uncertain_text(raw)
    assert "confirmed" not in sanitized.lower()
    assert "certainly is" not in sanitized.lower()
    assert "supported by available evidence" not in sanitized.lower()
    assert "bearing failure" in sanitized.lower()


def test_report_builder_includes_quality_telemetry():
    state = _make_state()
    report = build_single_report(state)
    assert report["evidence_score"] == 0.4
    assert report["history_score"] == 0.3
    assert report["fallback_used"] is False
    assert report["final_report_status"] == "specific"
    assert report["circuit_breaker_state"] == "CLOSED"


def test_build_evidence_chain_uses_existing_outputs():
    state = _make_state()
    chain = _build_evidence_chain(state)
    assert len(chain) == 3
    assert chain[0]["type"] == "sensor"
    assert "vibration" in chain[0]["evidence"]
    assert chain[1]["type"] == "history"
    assert chain[2]["type"] == "manual"
    assert chain[2]["source"] == "compressor_manual.pdf"


def test_explainability_score_100_when_all_sources_present():
    chain = [
        {"step": 1, "type": "sensor", "source": "s", "evidence": "e"},
        {"step": 2, "type": "history", "source": "s", "evidence": "e"},
        {"step": 3, "type": "manual", "source": "s", "evidence": "e"},
    ]
    assert _compute_explainability_score(chain) == 100


def test_explainability_score_lower_without_manual():
    chain = [
        {"step": 1, "type": "sensor", "source": "s", "evidence": "e"},
        {"step": 2, "type": "history", "source": "s", "evidence": "e"},
    ]
    # sensor (40) + history (30) = 70
    assert _compute_explainability_score(chain) == 70


def test_procurement_gap_detected_when_rul_short():
    # RUL 7 days < bearing lead time 21 days
    state = _make_state(rul_hours=7 * 24)
    pg = _compute_procurement_gap(state)
    assert pg["procurement_gap"] is True
    assert "bearing" in pg["recommended_action"].lower()


def test_procurement_gap_false_when_rul_long():
    # RUL 60 days > bearing lead time 21 days
    state = _make_state(rul_hours=60 * 24)
    pg = _compute_procurement_gap(state)
    assert pg["procurement_gap"] is False


def test_procurement_gap_scans_diagnosis_text():
    # Even if parts_required is empty, a bearing mention in root_cause should
    # still trigger a procurement gap when RUL is short.
    state = _make_state(rul_hours=7 * 24)
    state.strategy.parts_required = []
    state.diagnosis.root_cause = "Bearing wear"
    pg = _compute_procurement_gap(state)
    assert pg["procurement_gap"] is True
    assert any("bearing" in p["part"].lower() for p in pg["at_risk_parts"])


def test_procurement_gap_normalises_thermal_gasket():
    # The lead-time key is "thermal gasket"; free-text "thermal_gasket" must match.
    state = _make_state(rul_hours=7 * 24)
    state.strategy.parts_required = ["OEM thermal_gasket set"]
    pg = _compute_procurement_gap(state)
    assert pg["procurement_gap"] is True
    assert any("thermal" in p["part"].lower() for p in pg["at_risk_parts"])


def test_report_builder_passes_through_explainability_fields():
    state = _make_state()
    state.final_report = {
        **(state.final_report or {}),
        "evidence_chain": [{"step": 1, "type": "sensor", "source": "s", "evidence": "e"}],
        "explainability_score": 40,
        "procurement_gap": {"procurement_gap": False},
    }
    report = build_single_report(state)
    assert report["evidence_chain"] == [{"step": 1, "type": "sensor", "source": "s", "evidence": "e"}]
    assert report["explainability_score"] == 40
    assert report["procurement_gap"] == {"procurement_gap": False}
